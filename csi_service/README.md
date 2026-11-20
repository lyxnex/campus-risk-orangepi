# CSI 串口采集独立服务（csi_service/）

自包含的 ESP32 CSI 串口采集服务，与 campus-risk 音频链路（`edge/`）
**零代码、零运行时耦合**：只依赖本目录的 `csi_parse.py`/`csi_stream.py`
和 numpy/pyserial，由独立 systemd 单元运行，与音频服务的启停、更新互不
影响。可整体拷贝到任何接入 CSI 板（CH343 USB 串口）的机器使用。

## 目录内容

| 文件 | 作用 |
| --- | --- |
| `csi_capture.py` | 服务主程序：3 秒滑动窗口切样本（步进 2.5 秒即重叠 0.5 秒）落盘；内含磁盘水位暂停、过期样本兜底清理、状态文件。 |
| `csi_stream.py` | 串口读流基础设施（只开一次串口、读线程、健康统计、样本落盘、显眼告警）。 |
| `csi_parse.py` | esp-csi CSI_DATA CSV 行解析。 |
| `csi_collect.py` | 二分类数据集交互采集脚本（**暂缓维护**，现阶段不使用，保留备用；需手动命令运行）。 |
| `csi_service.example.json` | 配置示例；实际配置 `csi_service.json` 不进 Git。 |
| `csi-capture.service` | systemd 单元模板；ExecStart 路径按实际安装位置修改。 |

## 串口选择：auto（推荐）与手动指定

配置里 `port` 推荐保持 `"auto"`（省略该键效果相同）：

- **自动探测**只认 WCH CH34x 芯片（VID 0x1a86，CSI 发送板的 CH343），
  不会误碰其他 USB 设备（如麦克风的 Espressif 自带 USB）；多个候选口时
  逐口嗅探 `CSI_DATA` 标志，选出真正在发 CSI 数据的那个，被占用的口
  自动跳过。
- **换 USB 口、重新插拔、甚至重启后 tty 编号漂移都不影响**：实测本机
  CSI 板曾从 `ttyACM1` 漂到 `ttyACM0`，auto 每次启动重新探测，插上即认，
  无需改配置；服务因 stall/崩溃重启时也会重新探测，换一块 CH34x 的
  ESP32 同样自动适配。
- 手动指定 `"/dev/serial/by-id/..."` 的场景：机器上有非 CSI 用途的
  CH34x 串口需要精确防呆，或 CSI 板用的不是 WCH 芯片（auto 认不出，
  必须手动指定）。by-id 路径绑定具体那块板（含序列号），换 USB 口不变，
  换板子会变。
- 探测不到任何 CH34x 口时：显眼告警（检查供电 / 重插 USB / 换线）并
  退出，由 systemd 5 秒后重试。

## 采集与保存

- 样本：`csi_<session>_<NNNN>.npz + .json`，npz 含 `csi`(complex64 N×128，
  本项目固件仅开 htltf_en；宽度按固件实际自适应) / amp / phase / iq /
  时间戳与元数据，及自描述字符串 `format_version: csi_sample_v1`、
  `ltf_segments`、`iq_layout`；sidecar JSON 写完样本才算就绪。
- 默认落盘位置：配置的 `out_dir`（示例为 `/home/orangepi/csi-data/csi_samples`）。
- 状态：`status_file`（示例 `/home/orangepi/csi-data/status.json`），含
  `state`（sampling / paused_low_disk / stopped）、样本数、最近样本路径。
- 健康保护：串口 stall 5 秒 → 显眼告警（按 RST / 重插 USB 等建议）退出，
  systemd 5 秒重启重连；磁盘低于 `min_free_gib` 暂停落盘并告警。

## 样本格式与未来模型接入点

- CSI 数据暂不参与暴力检测；`csi_capture.py` 只负责采集落盘，不含推理。
- 未来模型接入方式与音频链路同构：推理循环轮询 `out_dir` 下 `*.npz`
  （sidecar `.json` 存在即视为样本就绪），处理后写 `.handled.json` 标记。
- 设想为推理完成后无异常即删样本；`retention_hours`（默认 0 = 不清理）
  仅作无人值守时的时间兜底。
- 采集与训练数据格式统一：数据集采集脚本（见下）产出相同 npz 格式。

## 数据集采集（暂缓维护）

`csi_collect.py` 是部署前实地二分类数据集采集脚本（暴力/非暴力），
**当前暂缓维护**：设备实地部署后只能远程控制，当地人员不会上机运行
采集脚本，数据集采集暂不使用，脚本保留备用。恢复使用时需要
`pyfiglet`（大字提示用，缺失时自动回退 `##` 框，不影响功能）。

```bash
# 整轮只采一个标签，每样本默认 3 秒、间休 1 秒，大字倒计时 + 板载 LED
# （绿=录制 / 蓝=间歇）提示，输出 ./csi_dataset/（含标注日志与质量 CSV）
.venv/bin/pip install pyfiglet   # 可选，大字提示
.venv/bin/python csi_collect.py --non-violence -n 20 --port /dev/serial/by-id/usb-1a86_...
.venv/bin/python csi_collect.py --violence     -n 20
```

## 安装（首次）

```bash
cd <安装路径>/csi_service
python3 -m venv .venv
.venv/bin/pip install numpy pyserial   # pyfiglet 仅数据集采集脚本需要，暂缓维护可不装
cp csi_service.example.json csi_service.json   # 填入实际串口与保存路径
# 编辑 csi-capture.service 的 ExecStart 路径指向实际安装位置
sudo cp csi-capture.service /etc/systemd/system/
sudo systemctl daemon-reload
```

## 注册成服务、启停与开机自启

注册（首次安装，见上节）= 把 unit 文件放进 systemd 并重载：

```bash
sudo cp csi-capture.service /etc/systemd/system/
sudo systemctl daemon-reload
```

启动 / 停止（这就是"保存开关"，只影响当前运行）：

```bash
sudo systemctl start csi-capture   # 启动，开始采集落盘
sudo systemctl stop  csi-capture   # 停止采集
```

开机自启（只影响开机行为，与当前是否运行互不影响）：

```bash
sudo systemctl enable  csi-capture   # 打开开机自启（不会立即启动，需另 start）
sudo systemctl disable csi-capture   # 关闭开机自启（不会停止当前运行）
sudo systemctl enable --now csi-capture   # 打开自启并立即启动，一步到位
```

> 测试阶段：只 `start` 不 `enable`，重启后服务不会自己起来；正式部署时
> `enable --now` 一次到位。

状态检查与日志：

```bash
systemctl status csi-capture              # 运行状态（active/failed）
systemctl is-enabled csi-capture          # 是否已设开机自启
journalctl -u csi-capture -f              # 跟踪日志（CSI_SAMPLE_OK / 告警）
cat /home/orangepi/csi-data/status.json   # 采集状态文件（远程检查入口）
```

从机器上移除服务（不再使用时）：

```bash
sudo systemctl disable --now csi-capture  # 停止并取消开机自启
sudo rm /etc/systemd/system/csi-capture.service
sudo systemctl daemon-reload
```

服务未 start / 未 enable 时 CSI 不采集——测试阶段保持默认不启动即可。

## 手动命令运行（调试用，不装服务也能跑）

```bash
.venv/bin/python csi_capture.py --config csi_service.json --seconds 60
# 或完全用命令行参数：
.venv/bin/python csi_capture.py --port /dev/serial/by-id/usb-1a86_... \
    --out-dir /tmp/csi_test --seconds 20
```

## 远程获取数据

样本只保存在板端 `out_dir`。测试阶段通过 SSH/Tailscale 拉取：

```bash
rsync -av orangepi@<板地址>:/home/orangepi/csi-data/csi_samples/ ./csi_samples/
```

自动上传中心属于未来工作，尚未实现。
