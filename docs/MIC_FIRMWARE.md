# ESP32S3 麦克风固件与连接

本项目的采音路径是 **XMOS 麦克风板 → ESP32S3 → ESP USB → 香橙派**。香橙派连接 ESP 的 USB 串口，不使用 XMOS USB 直连路径。主机采集代码要求 `RUSBAUT2` 协议和固件状态 `usb_auto_v2_arq`；旧协议固件不能直接配合当前服务。

## 已交付的文件

`firmware/esp32s3/xiao_usb_arq.ino` 是固件源码；同目录四个 `*_flashed.bin` / `boot_app0_flashed.bin` 是在已测试 XIAO ESP32S3 8 MB 设备上使用的四段镜像。`manifest.json` 记录每段地址、大小及 SHA256，`firmware/flash_esp32s3.ps1` 在 Windows 上逐项校验并刷写。源码构建时观察到 ESP32 Arduino core `3.3.10-cn`、`audio-tools 1.2.6` 和清单中的 XIAO ESP32S3 FQBN；如重编译，须重新做协议和录音测试，不能假定新二进制与已验收镜像相同。

## 为一支全新的同型号麦克风刷写

先在 Windows 上仅连接**目标麦克风的 ESP USB**，确认 COM 端口、ESP32-S3 芯片、独立 MAC 和 8 MB flash。准备 esptool 5.3.0 的 `esptool.exe`。在仓库根目录打开 PowerShell，用实际端口、MAC 和工具路径执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\firmware\flash_esp32s3.ps1 -Port COM10 -ExpectedMac AA:BB:CC:DD:EE:FF -EsptoolPath C:\tools\esptool.exe
```

这里的 COM10/MAC 是占位示例，新设备必须填写自己的值。脚本先核对固件文件 SHA256，读取目标 MAC 和 flash 容量，保存该设备完整 8 MB 原始 flash 到被 Git 忽略的 `private/mic-backups/`，随后写入 `0x0`、`0x8000`、`0xe000`、`0x10000` 四段。不会执行全片擦除，也不会写入 XMOS 芯片。若目标不符、备份失败或备份文件已存在，脚本停止，保留已有文件。**原始完整 flash 备份含设备状态，只能本地留存，不能作为另一支麦克风的镜像上传或克隆。**

已在两支不同身份的同型号麦克风上验证了这四段镜像；当前接在新香橙派的第二支麦克风已配置好，无需重刷。脚本用于以后新增同型号设备，不能无检查地用于不同硬件或容量。

## 接到香橙派并验证

将 ESP USB 接到新香橙派，在板端运行：

```bash
ls -l /dev/serial/by-id/
```

把实际 `usb-Espressif_...` 持久路径写入该板私密 `deployment_config.json` 的 `port`，勿照抄其他设备路径或 Windows COM 号。启动板端服务后，应观察 `capture=armed`（这表示已看到固件状态、VAD 正常并完成 ARM 握手），再做一次安静、讲话、安静的现场测试，确认生成录音且 `inference=ready` 并处理完成。无需把真实录音提交 GitHub。
