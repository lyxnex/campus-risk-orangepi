#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""csi_collect.py — 暴力/非暴力二分类 CSI 数据集采集（部署前实地数据采集模式）。

在设备安装好后、正式部署前运行：整轮只采一个标签，每个样本固定时长
（默认 3 秒），样本间默认休息 1 秒，全程有终端大字倒计时提示和板载 LED
指示（Orange Pi 5 Pro，绿/蓝两灯软件可控，红灯为电源指示灯不可操作）：

  绿灯常亮 = 正在录制
  蓝灯常亮 = 人员间歇 / 开发板已就绪

串口整个采集过程只开一次，每个样本是从同一条连续字节流切出的定长段。

用法（edge 环境）：
  python csi_collect.py --non-violence -n 20
  python csi_collect.py --violence     -n 20 --rest 8
可选：--port --baud --duration 3 --prompt-time 2 --expected-rate 100
      --gap-threshold 0.5 --out-dir csi_dataset --all-ltfs --no-led

输出（--out-dir 下）：
  csi_dataset/csi_<时间>_binary_<NNNN>_<标签>.npz + .json
    （npz 格式与部署模式 csi_capture.py 完全一致，sidecar 另含 label）
  csi_dataset/logs/csi_<时间>_binary_00001_<标签>_annotation.log
    （标注日志：session_start / sample_start / sample_end / session_end）
  csi_dataset/logs/quality_report_binary.csv（逐样本质量报告）

流程复制自 serial/collect_binary.py（serial 目录计划删除，本文件为唯一副本）。
"""
import argparse
import glob
import math
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from csi_stream import (DEFAULT_BAUD, CSIStreamReader, print_alert,
                        write_sample_npz)

# ============================================================
# 标签定义：二分类（名称 → 数值标签）
# ============================================================
LABEL_TABLE = {'non-violence': 0, 'violence': 1}

LINK_ADVICE = (
    '确认 CSI 发送板已上电且在接收范围内',
    '按下 ESP32 的 RST 复位键',
    '重新插拔 ESP32 的 USB 线（插拔后设备名可能变化）',
    '确认没有其他程序占用该串口',
)


def _fmt_t(t):
    """unix 时间戳，保留 3 位小数（标注格式与 collect_binary 一致）。"""
    return f'{t:.3f}'


def _anno(anno_f, line):
    anno_f.write(line + '\n')
    anno_f.flush()


# ============================================================
# ASCII 大字横幅（pyfiglet 可选；含中文时回退 ## 框）
# ============================================================
try:
    import pyfiglet
except ImportError:
    pyfiglet = None

_FIGLET_FONTS = ('ansi_regular', 'ansi_shadow', 'colossal', 'block', 'doom', 'big')


def figlet_text(text, scale=1):
    """渲染 ASCII 大字；含非 ASCII 或未装 pyfiglet 时返回 None。"""
    if pyfiglet is None or not text.isascii():
        return None
    art = None
    for font in _FIGLET_FONTS:
        try:
            art = pyfiglet.figlet_format(text, font=font, width=300)
        except Exception:
            art = None
        if art and art.strip():
            break
        art = None
    if not art:
        return None
    lines = art.rstrip('\n').splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    if scale == 2:
        lines = [''.join(c * 2 for c in ln) for ln in lines]
        doubled = []
        for ln in lines:
            doubled += [ln, ln]
        lines = doubled
    return '\n'.join(lines)


def big_banner(text, scale=1):
    """大号显示：ASCII 用 figlet 字体，含中文时退回 ## 框。"""
    art = figlet_text(text, scale=scale)
    if art is None:
        line = '#' * (len(text) * 2 + 6)
        print(line)
        print(f'##  {text}  ##')
        print(line)
    else:
        print(art)


def show_prompt(label, label_value, duration, idx, total):
    """大字提示下一个样本。"""
    print('\033[2J\033[H', end='')     # 清屏
    print('╔══════════════════════════════════════════════════════╗')
    print('║                                                      ║')
    print(f'║   下一个样本：{idx}/{total} —— {label}')
    print(f'║   持续时间：{duration:g} 秒')
    print(f'║   标签值：{label_value}（{label}）')
    print('║                                                      ║')
    print('╚══════════════════════════════════════════════════════╝')
    print()
    big_banner(label)
    print()


def countdown(reader, duration, title, packets):
    """录制倒计时：逐秒大字重绘，轮询读流并把包收进 packets。

    轮询周期 ≤40ms，数据随到随收；显示重绘只在整秒变化时进行。
    串口 3 秒无有效包时弹出显眼告警框并停留数秒再恢复重绘。
    告警基准取 max(last_pkt_time, 本轮录制开始时刻)：样本切换时 drain()
    丢弃了段间积压包，last_pkt_time 可能仍是上一样本的旧时刻，不能据此
    判定本样本链路中断。
    """
    t_record_start = time.time()
    end = t_record_start + duration
    last_shown = None
    redraw_hold_until = 0.0
    while True:
        remaining = end - time.time()
        if remaining <= 0:
            break
        packets.extend(reader.poll(timeout=0.02))
        now = time.time()
        # 串口链路检查：本轮录制开始后超过 3 秒没有任何有效包 → 显眼告警
        reference = max(reader.last_pkt_time or 0.0, t_record_start)
        if now - reference > 3.0 and now >= redraw_hold_until:
            print_alert('录制中串口已 3 秒无 CSI 数据，本样本可能不完整', LINK_ADVICE)
            redraw_hold_until = now + 5
        if now < redraw_hold_until:
            time.sleep(0.02)
            continue
        rem = max(1, math.ceil(remaining))
        if rem != last_shown:
            last_shown = rem
            print('\033[2J\033[H', end='')
            print(f'===== [RECORDING] {title} — 剩余 {rem}s =====')
            print()
            big_banner(title)
            print()
            big_banner(str(rem), scale=2)
        time.sleep(0.02)
    print()


# ============================================================
# 板载 LED 控制（Orange Pi 5 Pro，手册 3.5 节 P192）
#   /sys/class/leds/{green,blue}_led：trigger（none/heartbeat...）+ brightness
#   写属性通常需要 root：依次回退 直接写 → sudo -n → 禁用（不影响采集）
# ============================================================
_LED_GLOBS = {'green': '/sys/class/leds/*green*', 'blue': '/sys/class/leds/*blue*'}


class BoardLED:
    """开发板状态灯：录制=绿常亮，间歇/就绪=蓝常亮；close() 恢复原 trigger。"""

    def __init__(self, enabled=True):
        self.ok = False
        self._use_sudo = False
        self._saved_trig = {}       # color -> 原 trigger 名（默认 heartbeat）
        self.paths = {}
        if not enabled:
            return
        for color, pat in _LED_GLOBS.items():
            hits = sorted(glob.glob(pat))
            if hits:
                self.paths[color] = hits[0]
        if 'green' not in self.paths or 'blue' not in self.paths:
            print('提示：未找到板载绿/蓝 LED（非 Orange Pi 5 Pro 环境？），'
                  '本次运行不控制 LED（--no-led 可手动禁用）。')
            return
        # 探测写权限：直接写 → sudo -n → 放弃
        probe = sorted(self.paths.values())[0] + '/brightness'
        if self._probe_write(probe):
            self.ok = True
        elif subprocess.run(['sudo', '-n', 'true'],
                            capture_output=True).returncode == 0:
            self._use_sudo = True
            self.ok = True
        else:
            print('提示：LED 属性无写权限且无免密 sudo——在开发板上执行 '
                  'setup_orangepi.sh 安装权限规则后重试，本次不控制 LED。')
            return
        # 记录当前 trigger（一般为 heartbeat），退出时恢复
        for color, path in self.paths.items():
            try:
                with open(path + '/trigger') as f:
                    content = f.read()
                m = re.search(r'\[([^]]+)\]', content)
                self._saved_trig[color] = m.group(1) if m else 'none'
            except OSError:
                self._saved_trig[color] = 'heartbeat'

    @staticmethod
    def _probe_write(path):
        try:
            with open(path) as f:
                int(f.read().strip())
            return os.access(path, os.W_OK)
        except OSError:
            return False

    def _write(self, path, value):
        """写 LED 属性，best-effort：失败返回 False，不抛异常。"""
        try:
            if self._use_sudo:
                r = subprocess.run(['sudo', '-n', 'tee', path],
                                   input=value + '\n', text=True,
                                   capture_output=True, check=True)
            else:
                with open(path, 'w') as f:
                    f.write(value + '\n')
            return True
        except (OSError, subprocess.CalledProcessError):
            return False

    def _lamp(self, color, on):
        path = self.paths.get(color)
        if path is None or not self.ok:
            return
        self._write(path + '/trigger', 'none')
        self._write(path + '/brightness', '255' if on else '0')

    def recording(self):
        """录制中：绿常亮、蓝灭。"""
        self._lamp('green', True)
        self._lamp('blue', False)

    def ready(self):
        """间歇/就绪：蓝常亮、绿灭。"""
        self._lamp('blue', True)
        self._lamp('green', False)

    def close(self):
        """恢复上电默认状态（原 trigger，一般为 heartbeat 闪烁）。"""
        if not self.ok:
            return
        for color, path in self.paths.items():
            self._write(path + '/trigger', self._saved_trig.get(color, 'heartbeat'))
        self.ok = False


# ============================================================
# 段末质量报告（沿用 collect_binary 的格式，写入独立 CSV）
# ============================================================
QUALITY_COLUMNS = ['run', 'session', 'seq', 'type', 'name', 'label',
                   'dat_file', 'n_packets', 'rate_hz', 'host_rate_hz',
                   'host_max_interval_ms', 'mean_interval_ms',
                   'median_interval_ms', 'p95_interval_ms', 'max_interval_ms',
                   'std_interval_ms', 'id_gaps', 'time_gaps', 'host_gaps',
                   'invalid_lines', 'rate_ok', 'gap_ok', 'uniform_ok']


def print_report(report):
    """打印段末质量报告（采样率 / 间隔均匀性 / 断号）。"""
    if report is None:
        return
    marks = ('✓' if report['rate_ok'] else '⚠',
             '✓' if report['gap_ok'] else '⚠',
             '✓' if report['uniform_ok'] else '⚠')
    print(f'  [质量] {report["stem"]}')
    print(f'    包数 {report["n_packets"]}，固件采样率 {report["rate_hz"]:.1f} Hz'
          f'（无线/发送链路），主机到达率 {report["host_rate_hz"]:.1f} Hz'
          f'（串口/处理链路）')
    print(f'    固件间隔 ms: 均值 {report["mean_interval"]*1e3:.1f} / '
          f'中位 {report["median_interval"]*1e3:.1f} / '
          f'P95 {report["p95_interval"]*1e3:.1f} / '
          f'最大 {report["max_interval"]*1e3:.1f} / '
          f'标准差 {report["std_interval"]*1e3:.1f}')
    print(f'    主机最大到达间隔 {report["host_max_interval"]*1e3:.1f} ms，'
          f'主机空窗 {report["n_host_gaps"]}')
    print(f'    id断号 {report["n_id_gaps"]}，空窗 {report["n_time_gaps"]}，'
          f'无效行 {report["n_invalid"]} | '
          f'采样率 {marks[0]} 空窗 {marks[1]} 均匀性 {marks[2]}')
    if (not report['rate_ok'] and report['n_host_gaps'] == 0
            and report['host_rate_hz'] >= report['rate_hz'] * 0.95):
        print('    提示：主机链路无丢包，丢包发生在固件侧——当前波特率下每行 CSI '
              '打印耗时偏长，是采样率上限。确认固件控制台波特率为 2000000'
              '（menuconfig 烧录接收板）后可回到 ~100Hz。')


def append_quality_row(csv_path, run, seq, name, label_value, stem, report):
    """追加一行样本质量报告到 quality_report_binary.csv。"""
    new_file = not os.path.exists(csv_path)
    with open(csv_path, 'a', encoding='utf-8', newline='') as f:
        if new_file:
            f.write(','.join(QUALITY_COLUMNS) + '\n')
        r = report or {}
        row = [run, 'binary', seq, 'sample', name, label_value, stem,
               r.get('n_packets', 0), f'{r.get("rate_hz", 0):.2f}',
               f'{r.get("host_rate_hz", 0):.2f}',
               f'{r.get("host_max_interval", 0)*1e3:.2f}',
               f'{r.get("mean_interval", 0)*1e3:.2f}',
               f'{r.get("median_interval", 0)*1e3:.2f}',
               f'{r.get("p95_interval", 0)*1e3:.2f}',
               f'{r.get("max_interval", 0)*1e3:.2f}',
               f'{r.get("std_interval", 0)*1e3:.2f}',
               r.get('n_id_gaps', 0), r.get('n_time_gaps', 0),
               r.get('n_host_gaps', 0), r.get('n_invalid', 0),
               int(r.get('rate_ok', False)), int(r.get('gap_ok', False)),
               int(r.get('uniform_ok', False))]
        f.write(','.join(str(v) for v in row) + '\n')


# ============================================================
# 主程序
# ============================================================

def parse_args():
    ap = argparse.ArgumentParser(
        description='暴力/非暴力二分类 CSI 数据集采集（部署前实地数据采集；'
                    '每样本固定时长默认 3 秒、间休 1 秒，板载 LED 指示状态）')
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument('--violence', dest='label', action='store_const',
                       const='violence', help='本轮标签：暴力（label=1）')
    group.add_argument('--non-violence', dest='label', action='store_const',
                       const='non-violence', help='本轮标签：非暴力（label=0）')
    ap.add_argument('-n', '--samples', type=int, default=10,
                    help='本轮样本数（默认 10）')
    ap.add_argument('--duration', type=float, default=3.0,
                    help='每样本录制时长秒（默认 3，与部署模式样本窗长一致）')
    ap.add_argument('--rest', type=float, default=1.0,
                    help='样本间休息秒（默认 1，蓝灯亮）')
    ap.add_argument('--port', help='串口名（默认自动选择 CH343/CH34x 口；'
                                   '推荐 /dev/serial/by-id/ 持久路径）')
    ap.add_argument('--baud', type=int, default=DEFAULT_BAUD,
                    help=f'波特率（默认 {DEFAULT_BAUD}）')
    ap.add_argument('--probe-timeout', type=float, default=2.0,
                    help='多串口候选时每个口的探测时长秒（默认 2）')
    ap.add_argument('--prompt-time', type=float, default=2.0,
                    help='每样本前大字提示停留秒数（默认 2）')
    ap.add_argument('--expected-rate', type=float, default=100.0,
                    help='名义采样率 Hz（默认 100，即发送端 CONFIG_SEND_FREQUENCY）；'
                         '质量报告按其 80%% 判定采样率达标')
    ap.add_argument('--gap-threshold', type=float, default=0.5,
                    help='采集空窗判定阈值秒（默认 0.5）')
    ap.add_argument('--out-dir', default='csi_dataset',
                    help='数据与标注日志输出目录（默认 ./csi_dataset）')
    ap.add_argument('--all-ltfs', action='store_true',
                    help='npz 不做段裁剪：三段固件(len=384)保存全部三段；'
                         '本项目固件(len=256)数据本就完整，此参数无效果')
    ap.add_argument('--no-led', action='store_true',
                    help='禁用板载 LED 控制')
    return ap.parse_args()


def main():
    args = parse_args()

    try:    # 重定向输出时防止特殊字符（块状大字）触发编码错误
        sys.stdout.reconfigure(errors='replace')
    except Exception:
        pass

    label_name = args.label
    label_value = LABEL_TABLE[label_name]
    if args.samples < 1:
        print('样本数必须 >= 1')
        sys.exit(1)
    if args.duration <= 0:
        print('样本时长必须 > 0')
        sys.exit(1)

    data_dir = args.out_dir
    log_dir = os.path.join(args.out_dir, 'logs')
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(log_dir, exist_ok=True)
    quality_csv = os.path.join(log_dir, 'quality_report_binary.csv')

    print()
    print('=' * 56)
    print(' 二分类 CSI 数据集采集（暴力/非暴力，部署前实地采集）')
    print(f' 标签:       {label_name}（label={label_value}）')
    print(f' 样本:       {args.samples} 个 × {args.duration:g}s，间休 {args.rest:g}s')
    print(f' 名义采样率: {args.expected_rate:.0f} Hz')
    print(f' 数据目录:   {os.path.abspath(data_dir)}')
    print(f' 标注目录:   {os.path.abspath(log_dir)}')
    print(f' 时间:       {time.strftime("%Y-%m-%d %H:%M:%S")}')
    print('=' * 56)
    print()

    # LED 先于串口初始化（权限探测失败只降级不阻断；探测不涉及串口）
    led = BoardLED(enabled=not args.no_led)
    reader = CSIStreamReader(device=args.port, baud=args.baud,
                             probe_timeout=args.probe_timeout,
                             gap_threshold=args.gap_threshold,
                             expected_rate=args.expected_rate)
    print('打开串口（整个采集过程只开一次，请耐心等待首包稳定）...')
    reader.open()
    print(f'串口就绪: {reader.device} @ {args.baud}')
    if led.ok:
        print('板载 LED: 就绪=蓝灯常亮 / 录制=绿灯常亮，退出自动恢复')
    led.ready()
    print()

    run_ts = time.strftime('%Y%m%d_%H%M%S')
    anno_path = os.path.join(log_dir,
                             f'csi_{run_ts}_binary_00001_{label_name}_annotation.log')
    anno = open(anno_path, 'w', encoding='utf-8', newline='\n')
    anno_done = False
    interrupted = False
    try:
        _anno(anno, f'session_start, {_fmt_t(time.time())}, binary, '
                    f'label={label_name}({label_value}), samples={args.samples}, '
                    f'duration={args.duration:g}')
        print(f'===== 开始采集：{args.samples} 个样本，标签 {label_name} =====')
        print(f'  标注文件: {anno_path}\n')

        for i in range(1, args.samples + 1):
            stem_ts = time.strftime('%Y%m%d_%H%M%S')
            stem = f'csi_{stem_ts}_binary_{i:04d}_{label_name}'

            show_prompt(label_name, label_value, args.duration, i, args.samples)
            time.sleep(max(0.0, args.prompt_time))

            print(f'===== [START] 样本 {i}/{args.samples}: {label_name} '
                  f'({args.duration:g}s) =====')
            t_start = time.time()
            led.recording()
            # 新样本从"现在"开始：重置段统计并丢弃段间滞留数据
            reader.drain()
            reader.start_segment()
            packets = []
            _anno(anno, f'sample_start, {_fmt_t(t_start)}, {label_name}, '
                        f'label={label_value}, seq={i}, dat_file={stem}')

            countdown(reader, args.duration, f'{label_name} #{i}/{args.samples}',
                      packets)

            t_end = time.time()
            _anno(anno, f'sample_end, {_fmt_t(t_end)}, {label_name}, '
                        f'label={label_value}, seq={i}, dat_file={stem}')

            report = reader.segment_report(stem=stem)
            npz_path, _ = write_sample_npz(stem, data_dir, packets,
                                           report=report, all_ltfs=args.all_ltfs,
                                           label=label_name)
            led.ready()
            print(f'  已保存 {npz_path}')
            print_report(report)
            append_quality_row(quality_csv, 1, i, label_name, label_value,
                               stem, report)

            if i < args.samples:
                print(f'\n样本 {i}/{args.samples} 完成，休息 {args.rest:g}s'
                      f'（蓝灯=间歇）...')
                time.sleep(args.rest)

        _anno(anno, f'session_end, {_fmt_t(time.time())}, binary')
        anno.close()
        anno_done = True
        print(f'\n===== 本轮采集完成：{args.samples} 个样本 =====')
        print(f'  标注文件: {anno_path}')
    except KeyboardInterrupt:
        interrupted = True
    finally:
        reader.close()
        led.close()
        if interrupted and not anno_done:
            # 中断时补写 session_end（保持标注格式），控制台另行说明
            with open(anno_path, 'a', encoding='utf-8', newline='\n') as anno:
                _anno(anno, f'session_end, {_fmt_t(time.time())}, binary')
            print('\n[中断] 本轮提前结束，已完成样本的数据与标注已收尾保存。')
        print(f'\n质量报告: {quality_csv}')
        if interrupted:
            sys.exit(130)


if __name__ == '__main__':
    main()
