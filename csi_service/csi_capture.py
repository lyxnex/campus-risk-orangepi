#!/usr/bin/env python3
"""CSI 串口采集独立服务：持续读串口，按滑动时间窗切 3 秒样本落盘。

本目录（csi_service/）自包含：只依赖同目录的 csi_stream.py/csi_parse.py
和 numpy/pyserial，与 campus-risk 音频链路零代码、零运行时耦合。可整体
拷贝到任何接入 CSI 板的机器，由独立 systemd 单元（csi-capture.service）
或命令行直接运行，与音频服务的启停、更新互不影响。

从 ESP32 CSI 串口（CH343 USB CDC，esp-csi CSV 行协议）持续读取，
每 --sample-seconds 秒（默认 3）切出一个样本，相邻窗口起点相差
--hop-seconds 秒（默认 2.5），即样本间重叠 0.5 秒。样本以 npz + sidecar
JSON 落盘到 --out-dir；sidecar 写完样本才算就绪。

服务化职责（原由音频 edge_service 提供，现内置）：
  - 磁盘水位：低于 --min-free-gib 暂停落盘，恢复到 --resume-free-gib 继续
  - 过期样本清理：--retention-hours 大于 0 时按 sidecar saved_at 兜底清理
    （设想为未来推理完成后无异常即删样本，此参数仅兜底）
  - 状态文件：--status-file 周期写服务状态，供远程检查（journal 之外）

串口生命周期内只打开一次，样本全部从同一条连续字节流按时间窗切出。
超过 --stall-seconds 秒无有效 CSI 包时打印显眼告警并以非零码退出，
由 systemd（或人工）重启实现重连。
"""
import argparse
import collections
import datetime
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from csi_stream import (DEFAULT_BAUD, CSIStreamReader, autodetect_port,
                        print_alert, window_report, write_sample_npz)

SAMPLE_REPORT_ADVICE = (
    '确认 CSI 发送板仍在上电并靠近接收板',
    '按下 ESP32 的 RST 复位键',
    '重新插拔 ESP32 的 USB 线（插拔后设备名可能变化）',
    '确认没有其他程序占用该串口',
)


def parse_args():
    # 先解析 --config：JSON 提供全部参数默认值，命令行显式参数可覆盖。
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument('--config', type=Path, help='JSON 配置文件（键同下方参数名）')
    known, _ = pre.parse_known_args()
    defaults = {}
    if known.config is not None:
        with open(known.config, encoding='utf-8') as f:
            defaults = json.load(f)
    p = argparse.ArgumentParser(description=__doc__, parents=[pre])
    p.add_argument('--port', default='auto',
                   help='串口设备（默认 auto：自动探测 CH34x 口；'
                        '推荐 /dev/serial/by-id/ 持久路径）')
    p.add_argument('--baud', type=int, default=DEFAULT_BAUD)
    p.add_argument('--out-dir', type=Path, default=Path('csi_samples'))
    p.add_argument('--sample-seconds', type=float, default=3.0,
                   help='每个样本的时间窗长度秒（默认 3）')
    p.add_argument('--hop-seconds', type=float, default=2.5,
                   help='相邻样本窗口起点间隔秒（默认 2.5，即重叠 0.5 秒）')
    p.add_argument('--stall-seconds', type=float, default=5.0,
                   help='无有效包多少秒后告警退出（默认 5；由 systemd 重启重连）')
    p.add_argument('--gap-threshold', type=float, default=0.5,
                   help='采集空窗判定阈值秒（默认 0.5）')
    p.add_argument('--expected-rate', type=float, default=100.0,
                   help='名义采样率 Hz（质量报告按其 80%% 判定达标）')
    p.add_argument('--probe-timeout', type=float, default=2.0,
                   help='auto 探测时每个候选口的嗅探时长秒（默认 2）')
    p.add_argument('--seconds', type=float, default=0,
                   help='运行时长秒，0 表示连续运行（默认 0）')
    p.add_argument('--metrics-every', type=float, default=30,
                   help='CSI_STATUS 状态行与状态文件刷新间隔秒，0 禁用')
    p.add_argument('--min-free-gib', type=float, default=1.0,
                   help='磁盘剩余低于该值暂停落盘（默认 1.0）')
    p.add_argument('--resume-free-gib', type=float, default=2.0,
                   help='磁盘剩余恢复到该值继续落盘（默认 2.0）')
    p.add_argument('--retention-hours', type=float, default=0,
                   help='样本保留小时数，超过即清理（默认 0 = 不清理，仅兜底）')
    p.add_argument('--cleanup-every', type=float, default=300,
                   help='过期样本清理检查间隔秒（默认 300）')
    p.add_argument('--status-file', type=Path, default=None,
                   help='服务状态文件路径（默认 <out-dir>/status.json）')
    if defaults:
        p.set_defaults(**defaults)
    args = p.parse_args()
    if args.sample_seconds <= 0 or args.hop_seconds <= 0:
        p.error('sample-seconds and hop-seconds must be positive')
    if args.hop_seconds >= args.sample_seconds:
        p.error('hop-seconds must be smaller than sample-seconds (no overlap otherwise)')
    if args.stall_seconds <= 0 or args.seconds < 0:
        p.error('stall-seconds must be positive; seconds must be zero (continuous) or positive')
    if args.metrics_every < 0 or args.cleanup_every <= 0:
        p.error('metrics-every must not be negative; cleanup-every must be positive')
    if args.min_free_gib < 0 or args.resume_free_gib <= args.min_free_gib:
        p.error('resume-free-gib must exceed min-free-gib (both nonnegative)')
    if args.retention_hours < 0:
        p.error('retention-hours must not be negative (0 disables)')
    return args


def cleanup_expired(out_dir, retention_hours):
    """按 sidecar saved_at 清理过期样本（npz+json 同删）及崩溃残留半写文件。"""
    expire = retention_hours * 3600
    now = time.time()
    removed = 0
    for sidecar in Path(out_dir).glob('csi_*.json'):
        try:
            with open(sidecar, encoding='utf-8') as f:
                saved = datetime.datetime.fromisoformat(json.load(f)['saved_at']).timestamp()
            if now - saved >= expire:
                sidecar.with_suffix('.npz').unlink(missing_ok=True)
                sidecar.unlink(missing_ok=True)
                removed += 1
        except (OSError, ValueError, KeyError):
            continue
    for stale in list(Path(out_dir).glob('*.partial')) + list(Path(out_dir).glob('*.tmp')):
        try:
            if now - stale.stat().st_mtime >= max(expire, 3600):
                stale.unlink(missing_ok=True)
        except OSError:
            pass
    return removed


def write_status(path, state):
    """原子写服务状态文件，供远程检查（journalctl 之外）。"""
    try:
        tmp = Path(str(path) + '.tmp')
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError:
        pass


def main():
    args = parse_args()
    stop = {'requested': False}   # 信号处理只置标志，主循环负责收尾

    def halt(*_):
        stop['requested'] = True
    signal.signal(signal.SIGTERM, halt)
    signal.signal(signal.SIGINT, halt)

    device = args.port
    if device == 'auto':
        device = autodetect_port(args.baud, args.probe_timeout)
    reader = CSIStreamReader(device=device, baud=args.baud,
                             gap_threshold=args.gap_threshold,
                             expected_rate=args.expected_rate)
    reader.open()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    status_path = args.status_file or (args.out_dir / 'status.json')
    print(f'CSI_CAPTURE_READY {reader.device} @ {args.baud} '
          f'sample={args.sample_seconds:g}s hop={args.hop_seconds:g}s '
          f'(overlap={args.sample_seconds - args.hop_seconds:g}s) '
          f'out={args.out_dir.resolve()}', flush=True)

    session = datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    buffer = collections.deque()      # (host_time, pkt, t_rel)，按到达序单调
    window_start = None
    saved = 0
    last_sample = None
    started = time.time()
    last_metrics = started
    last_cleanup = started
    paused = False
    service_state = 'sampling'

    def report_status(state, free_gib=None):
        """打印 CSI_STATUS 状态行并同步状态文件（远程检查入口）。"""
        print('CSI_STATUS', json.dumps(dict(
            device=reader.device, state=state, samples=saved, qsize=reader.qsize(),
            total_packets=reader.total_packets, total_invalid=reader.total_invalid,
            csi_width=reader.csi_width, buffer_len=len(buffer),
            free_gib=None if free_gib is None else round(free_gib, 2),
            seconds=round(time.time() - started, 1))), flush=True)
        write_status(status_path, dict(
            service='csi_capture', state=state, device=reader.device,
            samples=saved, total_packets=reader.total_packets,
            total_invalid=reader.total_invalid, csi_width=reader.csi_width,
            last_sample=last_sample, free_gib=None if free_gib is None else round(free_gib, 2),
            out_dir=str(args.out_dir.resolve()),
            updated_at=datetime.datetime.now(datetime.timezone.utc).isoformat()))

    try:
        while not stop['requested']:
            now = time.time()
            free = shutil.disk_usage(args.out_dir).free / 1024**3
            # 磁盘水位：低则暂停落盘（数据丢弃），恢复后重新对齐窗口
            if not paused and free < args.min_free_gib:
                paused = True
                service_state = 'paused_low_disk'
                print_alert(f'磁盘仅剩 {free:.2f} GiB（低于 {args.min_free_gib:g} GiB），'
                            'CSI 采集暂停，样本暂不落盘', [
                    '清理磁盘空间（旧录音/旧样本/模型缓存）',
                    '打开 csi_service 配置的 retention_hours 自动清理',
                    '恢复到 resume-free-gib 后采集自动继续',
                ])
                window_start = None
                buffer.clear()
            elif paused and free >= args.resume_free_gib:
                paused = False
                service_state = 'sampling'
                print('CSI_RESUMED disk free %.2f GiB' % free, flush=True)
            if paused:
                for _ in reader.poll(timeout=0.2):
                    pass    # 消化队列保持链路健康统计，不落盘
                if args.metrics_every and now - last_metrics >= args.metrics_every:
                    last_metrics = now
                    report_status(service_state, free)
                if args.seconds and now - started >= args.seconds:
                    break
                time.sleep(0.2)
                continue
            for pkt, ts, t_rel in reader.poll(timeout=0.2):
                if window_start is None:
                    window_start = ts   # 首包对齐窗口起点
                buffer.append((ts, pkt, t_rel))
            # 链路健康：超时无有效包（含从未收到包的情况）
            reference = reader.last_pkt_time or reader.t_start
            if now - reference > args.stall_seconds:
                print_alert(f'CSI_STALLED: 串口已 {now - reference:.1f} 秒无有效 CSI 数据',
                            SAMPLE_REPORT_ADVICE)
                print('CSI_STALLED', reader.device, flush=True)
                raise SystemExit(1)
            # 滑动窗口切分：缓冲末尾越过窗口终点即产出该窗口样本
            while window_start is not None and buffer and \
                    buffer[-1][0] >= window_start + args.sample_seconds:
                end = window_start + args.sample_seconds
                sample = [(p, t, rel) for t, p, rel in buffer if t < end]
                report = window_report(sample, gap_threshold=args.gap_threshold,
                                       expected_rate=args.expected_rate)
                npz_path, _ = write_sample_npz(
                    f'csi_{session}_{saved + 1:04d}', str(args.out_dir), sample,
                    report=report)
                saved += 1
                last_sample = npz_path
                print('CSI_SAMPLE_OK', npz_path, 'packets', len(sample), flush=True)
                window_start += args.hop_seconds
                # 丢弃早于新窗口起点的包；重叠段（hop 之前的窗口尾部）保留
                while buffer and buffer[0][0] < window_start:
                    buffer.popleft()
            if args.retention_hours > 0 and now - last_cleanup >= args.cleanup_every:
                last_cleanup = now
                removed = cleanup_expired(args.out_dir, args.retention_hours)
                if removed:
                    print('CSI_RETENTION_REMOVED', removed, 'expired samples', flush=True)
            if args.seconds and now - started >= args.seconds:
                break
            if args.metrics_every and now - last_metrics >= args.metrics_every:
                last_metrics = now
                report_status(service_state, free)
    finally:
        report_status('stopped')
        reader.close()
    print('CSI_SESSION_END', saved, 'samples', flush=True)


if __name__ == '__main__':
    main()
