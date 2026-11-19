# 仓库文件说明

源码直接对应当前运行链路。大型模型从 Release 下载，现场配置和录音只保存在各自设备。本页按文件说明职责，便于继续开发。

| 路径 | 作用 |
| --- | --- |
| `README.md` | 项目入口、部署顺序和已验证范围。 |
| `.gitignore` | 防止私密配置、录音、模型与运行状态进入普通 Git 提交。 |
| `assets.lock.json` | 香橙派 13 项模型和 ARM runtime 的大小与 SHA256。 |
| `release_assets.json` | Release 大型附件的大小、SHA256 和用途。 |
| `source_manifest.json` | 从旧板和当前中心核对的源码哈希；新增工具/固件另有说明。 |
| `docs/board-snapshot.json` | 旧板平台、Python、动态库与现场导出摘要。 |
| `docs/NEW_BOARD_IMPORT.md` | 新香橙派从空白系统导入代码、资产和服务。 |
| `docs/CENTER_WINDOWS.md` | 新 Windows 中心、网页、CED 模型及设备配对。 |
| `docs/TAILSCALE_CROSS_NETWORK.md` | 跨局域网 tailnet 注册、登录、账号移交、地址与防火墙。 |
| `docs/CAMPUS_NETWORK.md` | 北邮 Portal 入网与可选的定时重认证。 |
| `docs/MIC_FIRMWARE.md` | ESP32S3 固件、独立备份、串口绑定。 |
| `docs/MIGRATION.md` | 总体迁移顺序与验收边界。 |
| `docs/GITHUB_UPLOAD.md` | 代码、Release 附件和不上传内容的范围。 |
| `docs/DEVELOPMENT.md` | 后续开发的调用链、改动点与核验方法。 |

## 香橙派运行代码

| 路径（均位于 `edge/campus_risk_deploy/`） | 作用 |
| --- | --- |
| `edge_service.py` | 三个长期运行线程：采音、推理、候选上传；写 `status.json` 并管理本地状态。 |
| `common.py` | 事件结构、哈希、JSON 原子写入等两端共享基础函数。 |
| `decision.py` | RBT3→Qwen 两级判决规则、策略名称、候选条件。 |
| `qwen_runtime.py` | 本地 llama-server 的模型路径、进程控制和 Qwen 请求。 |
| `qwen_system_prompt.txt` | 当前 Qwen 布尔风险判断提示词；变更需重新验证。 |
| `check_edge.py` | 板端环境与配置检查工具。 |
| `capture/capture.py` | ESP USB 连接、VAD/ARM 握手、15 秒音频片段写入。 |
| `capture/auto_protocol.py` | RUSBAUT2 帧解析、CRC 和音频片段结构。 |
| `capture/reliable_usb.py` | ACK、重发和重复帧处理。 |
| `capture/host_health.py` | 主机资源与温度健康信息。 |
| `capture/auto_inference.py` | SenseVoice 转写、RBT3 基础推理方法；Qwen 引擎继承其中部分方法。 |
| `capture/qwen_inference.py` | 0.715 门控、Qwen 复核与候选结果。 |

## Windows 中心和网页

| 路径（均位于 `center/`） | 作用 |
| --- | --- |
| `center.py` | HTTP 服务、上传包校验/落盘、网页 API、人工复核与 CED 后台启动。 |
| `dashboard.html` | 登录、事件列表、试听、下载、筛选与人工复核的完整前端。 |
| `web_auth.py` | 网页密码哈希、登录会话和限速；设备上传使用另一条 bearer token 机制。 |
| `set_web_password.py` | 已有中心本地交互设置/重置网页密码，不更换设备 token。 |
| `common.py` | 与板端相同的事件格式和文件操作。 |
| `decision.py` | 与板端相同的策略和候选校验规则。 |
| `capture/ced_observer.py` | 在中心对已上传录音补算 CED-mini 声学事件，不参与风险门控。 |

## 配置、安装和模型

| 路径 | 作用 |
| --- | --- |
| `config/center.example.json` | 中心公开配置示例；token 和密码占位，不可直接当正式配置。 |
| `config/edge.example.json` | 单板公开配置示例；串口和中心地址需要逐设备填入。 |
| `deploy/campus-risk-qwen35-clean-v2.service` | 香橙派 systemd 服务模板。 |
| `deploy/start_center_windows.ps1` | 在新电脑使用 `private/` 中的配置和 venv 前台启动中心。 |
| `deploy/install_center_autostart.ps1` | 在管理员 PowerShell 中登记开机计划任务并检查中心健康。 |
| `deploy/allow_center_windows.ps1` | 在管理员 PowerShell 中按中心/板 IP 限定 TCP 8765 入站。 |
| `requirements/edge.lock.txt` | 旧板已安装 Python 3.10/aarch64 包版本快照。 |
| `requirements/center-ced.txt` | 当前 Windows 中心 CED 直接依赖。 |
| `requirements/README.md` | 依赖清单的来源与限制。 |
| `model_manifests/ced-mini.json` | CED Release ZIP 和四份模型文件的校验清单。 |
| `firmware/esp32s3/xiao_usb_arq.ino` | ESP32S3 RUSBAUT2 固件源码。 |
| `firmware/esp32s3/*_flashed.bin`、`boot_app0_flashed.bin` | 经验证的四段 ESP 烧录镜像。 |
| `firmware/esp32s3/manifest.json` | ESP 源码/镜像哈希及烧录地址。 |
| `firmware/flash_esp32s3.ps1` | 目标身份核对、完整备份与四段刷写。 |

| 工具路径（`tools/`） | 作用 |
| --- | --- |
| `export_running_board.py` | 旧板只读导出源码和模型/依赖清单。 |
| `package_board_assets.py` | 从旧板导出的清单打包非 Qwen 的 12 项资产。 |
| `verify_assets.py` | 新板核对 13 项模型与 ARM runtime。 |
| `install_ced_assets.py` | 核对并解压 CED Release 附件。 |
| `init_site.py` | 交互生成新现场中心配置、随机 token 和各板私密配对文件。 |
| `verify_repository.py` | 核对旧板/中心源码镜像及随仓库发布的 ESP 固件哈希。 |

`assets/`、`private/`、`.venv/`、录音和队列是运行时内容，**不在 Git 中**。Release 三个附件分别是板端非 Qwen 资产、当前 Q8 GGUF、中心 CED-mini；目录位置与 SHA256 见清单。
