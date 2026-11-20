# -*- coding: utf-8 -*-
"""ESP32 CSI 串口读流基础设施（部署模式与数据采集模式共用）。

职责：自动探测/打开 CH343 串口（生命周期内只开一次）、读线程把字节流切行
入队、主线程 poll 批量解析、样本 npz + sidecar JSON 落盘、显眼健康告警。

复制自 serial/csi_logger.py 的对应实现（serial 目录计划删除，本文件为
板端唯一副本）；裁剪逻辑与原版 DatasetWriter._arrays() 保持一致。

CSI 样本格式（两种模式统一，训练数据与未来推理输入同格式）：
  csi(complex64 N×W) / amp / phase / iq(int16) / id / rssi / channel /
  sec_channel / local_timestamp_us / sig_len / first_word / host_time /
  t_rel / mac + 自描述字符串 ltf_segments、iq_layout（采集模式另有 label）。
  W 按会话实际子载波宽度自适应：本项目固件仅开 htltf_en（len=256）时
  W=128 数据本就完整不裁剪；len=384 三段布局默认裁剪 HT-LTF 段 W=64。
"""

import json
import os
import queue
import sys
import threading
import time
from datetime import datetime, timezone

import numpy as np
import serial
import serial.tools.list_ports

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from csi_parse import is_csi_line, parse_csi_line

DEFAULT_BAUD = 2000000

# npz/sidecar 自描述格式版本；未来模型推理按此校验样本格式
CSI_SAMPLE_FORMAT = 'csi_sample_v1'


# ---------------------------------------------------------------- 显眼告警

def _disp_len(text):
    """终端显示宽度：中文等全角字符按 2 列计，保证告警框右边框对齐。"""
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(c) in ('F', 'W') else 1
               for c in text)


def print_alert(title, advice=(), to_stderr=False):
    """健康检查告警：显眼的警告框 + 具体处置建议，两种模式共用。"""
    lines = ['!! ' + t for t in title.splitlines()]
    lines += ['!!', '!! 处置建议（按顺序尝试）:']
    lines += ['!!   %d. %s' % (i + 1, a) for i, a in enumerate(advice)]
    width = max(_disp_len(l) for l in lines) + 4
    box = ['', '!' * width, '!!' + ' ' * (width - 4) + '!!']
    for l in lines:
        box.append('!!  ' + l + ' ' * (width - 6 - _disp_len(l)) + '!!')
    box += ['!!' + ' ' * (width - 4) + '!!', '!' * width, '']
    text = '\n'.join(box)
    print(text, flush=True)
    if to_stderr:
        print(text, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- 串口选择

WCH_VID = 0x1a86          # 沁恒（WCH）USB VID：CH340/CH341/CH343

def _is_ch34x(port):
    """识别 CH34x 串口。

    Linux 下 CH343（VID:PID 1a86:55d3）由 cdc_acm 驱动挂为 /dev/ttyACM0，
    描述为 "USB Single Serial" 不含 CH343 字样，故按 VID 匹配；Windows
    按描述串匹配。注意麦克风 ESP32 是 Espressif 自家 VID，不会误匹配。
    """
    if getattr(port, 'vid', None) == WCH_VID:
        return True
    desc = (port.description or '').upper()
    return any(k in desc for k in ('CH343', 'CH340', 'CH341'))


def list_all_ports():
    return sorted(serial.tools.list_ports.comports(), key=lambda p: p.device)


def _probe_csi(device, baud, timeout_s=2.0):
    """打开串口嗅探 timeout_s 秒，判断是否在输出 CSI_DATA 数据。"""
    try:
        with serial.Serial(device, baud, bytesize=8, parity='N',
                           stopbits=1, timeout=0.1) as ser:
            ser.reset_input_buffer()
            buf = b''
            t0 = time.time()
            while time.time() - t0 < timeout_s:
                buf += ser.read(4096)
                if b'CSI_DATA' in buf:
                    return True
    except (serial.SerialException, OSError):
        return False
    return False


def autodetect_port(baud, probe_timeout):
    """自动选择串口：CH34x 候选口；多个时探测出真正发 CSI_DATA 的那个。"""
    candidates = [p for p in list_all_ports() if _is_ch34x(p)]
    if not candidates:
        print_alert('未找到 CH343/CH340/CH341 串口设备', [
            '确认 CSI 发送板已上电（看板子指示灯）',
            '重新插拔 ESP32 的 USB 线',
            '换一根 USB 线或换 USB 口',
            '仍找不到时用 --port 手动指定（Linux: /dev/ttyACM0 等）',
        ])
        raise SystemExit(1)
    if len(candidates) == 1:
        print(f'检测到串口 {candidates[0].device}（{candidates[0].description}）')
        return candidates[0].device
    print(f'检测到 {len(candidates)} 个 CH34x 串口，探测哪个在输出 CSI 数据'
          f'（每个最多等 {probe_timeout:.0f}s）...')
    for p in candidates:
        print(f'  探测 {p.device} ...', end='', flush=True)
        if _probe_csi(p.device, baud, probe_timeout):
            print(' 收到 CSI_DATA ✓')
            return p.device
        print(' 无数据')
    print_alert('没有端口探测到 CSI 数据，将使用第一个候选口', [
        '确认发送板固件在持续输出 CSI（复位后应有 CSI_DATA 行）',
        '按下 ESP32 的 RST 复位键后重试',
        '确认接收板固件控制台波特率为 2000000',
    ])
    return candidates[0].device


def open_serial(device, baud):
    """打开串口；失败时显眼提示占用/权限两类原因后退出。"""
    try:
        return serial.Serial(device, baud, bytesize=8, parity='N',
                             stopbits=1, timeout=0.05)
    except serial.SerialException as e:
        message = str(e)
        if 'Permission denied' in message:
            print_alert(f'无法打开串口 {device}：权限不足', [
                '将用户加入 dialout 组：sudo usermod -aG dialout $USER，然后重新登录',
                '或用 sudo 临时运行本命令验证',
            ], to_stderr=True)
        else:
            print_alert(f'无法打开串口 {device}：{message}', [
                '确认没有其他程序占用该串口（minicom/串口监视器/另一个采集进程）',
                '重新插拔 ESP32 的 USB 线',
                '拔插后用 ls /dev/ttyACM* 确认设备名是否变化',
            ], to_stderr=True)
        raise SystemExit(1)


# ---------------------------------------------------------------- 读线程

def reader_loop(ser, q, stop_event):
    """读线程：读字节 → 按 \\n 切行 → 打上到达时间戳 → 入队。

    高频运行，保证串口驱动缓冲从不过期；到达时间戳用于 host_time/t_rel，
    使其反映每个包的真实到达时刻（不受主线程批处理节奏影响）。
    """
    carry = bytearray()
    while not stop_event.is_set():
        try:
            chunk = ser.read(4096)   # 大块读：减少 GIL 竞争与系统调用次数
        except (serial.SerialException, OSError):
            break
        if not chunk:
            continue
        carry += chunk
        while True:
            idx = carry.find(b'\n')
            if idx < 0:
                if len(carry) > 65536:   # 异常防护：长时间无换行的垃圾数据
                    carry.clear()
                break
            q.put((bytes(carry[:idx]), time.time()))
            del carry[:idx + 1]


# ---------------------------------------------------------------- 读流会话

class CSIStreamReader:
    """CSI 串口读流会话：串口只开一次，poll() 批量返回解析后的包。

    与原版 CsiCollector 的区别：不落盘、不绘图，只负责「读行 → 解析 →
    返回包」并维护链路健康统计；落盘由调用方（csi_capture 滑动窗口 /
    csi_collect 定长段）用 write_sample_npz() 完成。
    """

    def __init__(self, device=None, baud=DEFAULT_BAUD, probe_timeout=2.0,
                 gap_threshold=0.5, expected_rate=100.0):
        self.device = device            # None = open() 时自动选口
        self.baud = baud
        self.probe_timeout = probe_timeout
        self.gap_threshold = gap_threshold
        self.expected_rate = expected_rate

        self.ser = None
        self.q = queue.Queue()
        self.stop_event = threading.Event()
        self.reader = None
        self.t_start = None             # open() 时刻，t_rel 基准

        # 段内统计（start_segment 重置；segment_report 汇总）
        self.n_packets = 0
        self.n_invalid = 0
        self.n_log = 0
        self.n_id_gaps = 0              # id 断号损失包数
        self.n_time_gaps = 0            # 固件时钟空窗次数（间隔 > gap_threshold）
        self.n_host_gaps = 0            # 主机到达空窗次数
        self.last_pkt_time = None       # 主机时钟：最近一个有效包（链路健康检查）
        self.csi_width = 0              # 首包起的复数子载波数（len//2）
        self.mac = ''
        self._fw_ts = None
        self._host_ts = None
        self._last_id = None
        self._intervals = []
        self._host_intervals = []
        self._time_gap_warned = False
        # 全程累计（跨段不清零）
        self.total_packets = 0
        self.total_invalid = 0

    # -- 生命周期 ----------------------------------------------
    def open(self):
        """打开串口并启动读线程（整个采集进程只调用一次）。"""
        if self.device is None:
            self.device = autodetect_port(self.baud, self.probe_timeout)
        self.ser = open_serial(self.device, self.baud)
        try:
            self.ser.set_buffer_size(rx_size=256 * 1024)
        except Exception:
            pass
        self.ser.reset_input_buffer()
        self.reader = threading.Thread(target=reader_loop,
                                       args=(self.ser, self.q, self.stop_event),
                                       daemon=True)
        self.reader.start()
        self.t_start = time.time()

    def start_segment(self):
        """开启新统计段（不触碰串口与队列）。

        与原版 CsiCollector.switch 一致：段间 id 大跳变不计入新段质量报告；
        队列滞留数据由调用方按需丢弃（部署模式滑动窗口天然衔接，采集模式
        在每个样本开始前丢弃段间滞留包）。
        """
        self.n_packets = self.n_invalid = self.n_log = 0
        self.n_id_gaps = self.n_time_gaps = self.n_host_gaps = 0
        self._fw_ts = None
        self._host_ts = None
        self._last_id = None
        self._intervals = []
        self._host_intervals = []
        self._time_gap_warned = False

    def close(self):
        """停读线程、关串口。"""
        self.stop_event.set()
        if self.reader is not None:
            self.reader.join(timeout=2)
        if self.ser is not None:
            self.ser.close()

    # -- 数据处理 ----------------------------------------------
    def poll(self, timeout=0.05):
        """取一批队列行并解析；返回 [(pkt, host_time, t_rel), ...]。"""
        batch = []
        try:
            batch.append(self.q.get(timeout=timeout))
            while True:
                batch.append(self.q.get_nowait())
        except queue.Empty:
            pass
        out = []
        for raw, ts in batch:   # ts = 读线程打点的到达时刻
            text = raw.decode('utf-8', errors='replace')
            if not is_csi_line(text):
                self.n_log += 1
                continue
            pkt = parse_csi_line(text)
            if pkt is None:
                self.n_invalid += 1
                self.total_invalid += 1
                continue
            # 固件时钟间隔统计（无线/发送链路的均匀性 / 空窗检测）
            fw_ts = pkt['timestamp_us']
            if self._fw_ts is not None:
                dt = (fw_ts - self._fw_ts) / 1e6
                if dt < 0:          # uint32 µs 计数器回绕（约 71.6 分钟）
                    dt += 2 ** 32 / 1e6
                self._intervals.append(dt)
                if dt > self.gap_threshold and not self._time_gap_warned:
                    print_alert(f'采集出现 {dt:.2f}s 数据空窗'
                                f'（阈值 {self.gap_threshold}s）', [
                        '检查 Wi-Fi 发送板是否仍在上报（看其指示灯）',
                        '按下 ESP32 的 RST 复位键',
                        '重新插拔 ESP32 的 USB 线',
                    ])
                    self._time_gap_warned = True
            self._fw_ts = fw_ts
            # 主机时钟间隔统计（串口/处理链路健康）
            if self._host_ts is not None:
                hdt = ts - self._host_ts
                self._host_intervals.append(hdt)
                if hdt > self.gap_threshold:
                    self.n_host_gaps += 1
            self._host_ts = ts
            if self._last_id is not None and pkt['id'] != self._last_id + 1:
                self.n_id_gaps += max(0, pkt['id'] - self._last_id - 1)
            self._last_id = pkt['id']
            if not self.csi_width:
                self.csi_width = len(pkt['csi'])
                self.mac = pkt['mac']
            self.n_packets += 1
            self.total_packets += 1
            self.last_pkt_time = ts
            out.append((pkt, ts, ts - self.t_start))
        return out

    def qsize(self):
        return self.q.qsize()

    def drain(self):
        """丢弃队列中滞留的未解析数据（段间到达的包不属于任何动作段）。"""
        while True:
            try:
                self.q.get_nowait()
            except queue.Empty:
                break

    def segment_report(self, stem=''):
        """段末质量报告：双时钟采样率/间隔统计与三项达标判定。

        rate_hz 基于固件时钟（无线/发送链路），host_rate_hz 基于主机到达
        时刻（串口/处理链路）；两者对照可定位问题层。
        """
        iv = np.asarray(self._intervals, dtype=np.float64)
        span = float(iv.sum()) if iv.size else 0.0
        # rate = (N-1)/span：span 只覆盖首末包之间的间隔
        rate = ((self.n_packets - 1) / span
                if span > 0 and self.n_packets >= 2 else 0.0)
        hiv = np.asarray(self._host_intervals, dtype=np.float64)
        hspan = float(hiv.sum()) if hiv.size else 0.0
        host_rate = ((self.n_packets - 1) / hspan
                     if hspan > 0 and self.n_packets >= 2 else 0.0)
        if iv.size:
            mean_i, med_i = float(iv.mean()), float(np.median(iv))
            p95_i = float(np.percentile(iv, 95))
            max_i, std_i = float(iv.max()), float(iv.std())
        else:
            mean_i = med_i = p95_i = max_i = std_i = 0.0
        ok = self.n_packets >= 2
        return {
            'stem': stem,
            'n_packets': self.n_packets,
            'rate_hz': rate,
            'host_rate_hz': host_rate,
            'host_max_interval': float(hiv.max()) if hiv.size else 0.0,
            'n_host_gaps': self.n_host_gaps,
            'mean_interval': mean_i, 'median_interval': med_i,
            'p95_interval': p95_i, 'max_interval': max_i,
            'std_interval': std_i,
            'n_id_gaps': self.n_id_gaps,
            'n_time_gaps': self.n_time_gaps,
            'n_invalid': self.n_invalid,
            'n_log': self.n_log,
            'rate_ok': bool(ok and rate >= 0.8 * self.expected_rate),
            'gap_ok': bool(ok and max_i <= self.gap_threshold),
            'uniform_ok': bool(ok and med_i > 0 and p95_i <= 2.0 * med_i),
            'csi_width': self.csi_width,
            'mac': self.mac,
        }


# ---------------------------------------------------------------- 样本落盘

def window_report(packets, gap_threshold=0.5, expected_rate=100.0, stem=''):
    """从单个窗口/样本的包列表直接计算质量统计（无状态、严格对应窗口内容）。

    部署模式滑动窗口使用本函数；数据采集模式定长段与读流处理天然对齐，
    用 CSIStreamReader.segment_report()。
    """
    n = len(packets)
    fw = [p['timestamp_us'] for p, _, _ in packets]
    host = [ts for _, ts, _ in packets]
    ids = [p['id'] for p, _, _ in packets]
    intervals = []
    for i in range(1, n):
        dt = (fw[i] - fw[i - 1]) / 1e6
        if dt < 0:          # uint32 µs 计数器回绕（约 71.6 分钟）
            dt += 2 ** 32 / 1e6
        intervals.append(dt)
    host_intervals = [host[i] - host[i - 1] for i in range(1, n)]
    iv = np.asarray(intervals, dtype=np.float64)
    hiv = np.asarray(host_intervals, dtype=np.float64)
    span = float(iv.sum()) if iv.size else 0.0
    rate = ((n - 1) / span if span > 0 and n >= 2 else 0.0)
    hspan = float(hiv.sum()) if hiv.size else 0.0
    host_rate = ((n - 1) / hspan if hspan > 0 and n >= 2 else 0.0)
    if iv.size:
        mean_i, med_i = float(iv.mean()), float(np.median(iv))
        p95_i = float(np.percentile(iv, 95))
        max_i, std_i = float(iv.max()), float(iv.std())
    else:
        mean_i = med_i = p95_i = max_i = std_i = 0.0
    ok = n >= 2
    return {
        'stem': stem,
        'n_packets': n,
        'rate_hz': rate,
        'host_rate_hz': host_rate,
        'host_max_interval': float(hiv.max()) if hiv.size else 0.0,
        'n_host_gaps': int((hiv > gap_threshold).sum()),
        'mean_interval': mean_i, 'median_interval': med_i,
        'p95_interval': p95_i, 'max_interval': max_i,
        'std_interval': std_i,
        'n_id_gaps': sum(max(0, ids[i] - ids[i - 1] - 1) for i in range(1, n)),
        'n_time_gaps': int((iv > gap_threshold).sum()),
        'mean_rssi': float(np.mean([p['rssi'] for p, _, _ in packets])) if n else None,
        'rate_ok': bool(ok and rate >= 0.8 * expected_rate),
        'gap_ok': bool(ok and max_i <= gap_threshold),
        'uniform_ok': bool(ok and med_i > 0 and p95_i <= 2.0 * med_i),
        'csi_width': len(packets[0][0]['csi']) if n else 0,
        'mac': packets[0][0]['mac'] if n else '',
    }


def _trim_arrays(csi_full, iq_full, all_ltfs):
    """按会话实际宽度裁剪，与原版 DatasetWriter._arrays() 逻辑一致。

    本项目固件仅开 htltf_en（len=256 → width=128）：数据本就完整，原样保存
    N×128；len=384 三段布局（width=192）默认裁剪 HT-LTF 段 N×64，
    all_ltfs=True 时保存全部三段 N×192。
    """
    width = csi_full.shape[1] if csi_full.size else 0
    if width == 192 and not all_ltfs:
        # 三段布局：默认只保留 HT-LTF 段（训练推荐），复数下标 64..127
        return (csi_full[:, 64:128], iq_full[:, 128:256],
                'csi[:,0:64]=HT-LTF (默认仅保存HT-LTF段, all_ltfs 保存全部三段)')
    if width == 192:
        return csi_full, iq_full, ('csi[:,0:64]=LLTF, csi[:,64:128]=HT-LTF, '
                                   'csi[:,128:192]=STBC-HT-LTF')
    if width == 128:
        # 固件仅开 htltf_en（len=256）：HT-LTF 原生 128 点频谱
        return csi_full, iq_full, ('csi=HT-LTF 原生频谱（40MHz HT-LTF 128 点 '
                                   'FFT，114 有效子载波；固件仅开 htltf_en）')
    return (csi_full, iq_full,
            f'csi=完整 buf（每包 {width} 复数，固件单段 LTF；段别由固件 '
            'lltf_en/htltf_en 配置决定）')


def write_sample_npz(stem, out_dir, packets, report=None, all_ltfs=False,
                     label=None, extra_meta=None):
    """把一个样本（[(pkt, host_time, t_rel), ...]）落盘 npz + sidecar JSON。

    先写 .partial 再 os.replace 原子改名；sidecar 写完样本才算就绪。
    返回 (npz 路径, sidecar 路径)。
    """
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    if packets:
        csi_full = np.stack([p['csi'] for p, _, _ in packets])
        iq_full = np.stack([p['iq'] for p, _, _ in packets])
    else:
        csi_full = np.zeros((0, 0), np.complex64)
        iq_full = np.zeros((0, 0), np.int16)
    csi, iq, seg_note = _trim_arrays(csi_full, iq_full, all_ltfs)
    arrays = {
        'csi': csi,                                   # complex64
        'amp': np.abs(csi).astype(np.float32),        # 幅度
        'phase': np.angle(csi).astype(np.float32),    # 相位
        'iq': iq,                                     # int16 原始值
        'id': np.asarray([p['id'] for p, _, _ in packets], np.int32),
        'rssi': np.asarray([p['rssi'] for p, _, _ in packets], np.int16),
        'channel': np.asarray([p['channel'] for p, _, _ in packets], np.int16),
        'sec_channel': np.asarray([p['sec_channel'] for p, _, _ in packets], np.int16),
        'local_timestamp_us': np.asarray([p['timestamp_us'] for p, _, _ in packets], np.int64),
        'sig_len': np.asarray([p['sig_len'] for p, _, _ in packets], np.int16),
        'first_word': np.asarray([p['first_word'] for p, _, _ in packets], np.int8),
        'host_time': np.asarray([ts for _, ts, _ in packets], np.float64),
        't_rel': np.asarray([t_rel for _, _, t_rel in packets], np.float64),
        'label': np.asarray(label if label is not None else ''),
        'mac': packets[0][0]['mac'] if packets else '',
        'ltf_segments': seg_note,
        'iq_layout': 'iq 偶数下标=虚部Q, 奇数下标=实部I (先虚后实)',
        'format_version': CSI_SAMPLE_FORMAT,
    }
    npz_path = os.path.join(out_dir, stem + '.npz')
    tmp_path = npz_path + '.partial'
    with open(tmp_path, 'wb') as f:
        np.savez(f, **arrays)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, npz_path)

    report = dict(report or {})
    host = [ts for _, ts, _ in packets]
    start = datetime.fromtimestamp(host[0], timezone.utc) if host else None
    end = datetime.fromtimestamp(host[-1], timezone.utc) if host else None
    meta = {
        'stem': stem,
        'npz': os.path.basename(npz_path),
        'format_version': CSI_SAMPLE_FORMAT,
        'label': label,
        'n_packets': len(packets),
        'csi_shape': list(csi.shape),
        'ltf_segments': seg_note,
        'mac': arrays['mac'],
        'mean_rssi': float(np.mean(arrays['rssi'])) if len(packets) else None,
        'window_start_utc': start.isoformat() if start else None,
        'window_end_utc': end.isoformat() if end else None,
        'window_start_local': start.astimezone().isoformat() if start else None,
        'window_end_local': end.astimezone().isoformat() if end else None,
        'report': report,
        'extra': extra_meta or {},
        'saved_at': datetime.now(timezone.utc).isoformat(),
    }
    json_path = os.path.join(out_dir, stem + '.json')
    tmp_json = json_path + '.tmp'
    with open(tmp_json, 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_json, json_path)
    return npz_path, json_path
