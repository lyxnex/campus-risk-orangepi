# 香橙派校园风险语言检测

这是从已运行版本整理到私人 GitHub 仓库的完整**源码与配置流程**。2026-11-18 对旧板做了只读导出，12 份板端源码与本目录逐文件 SHA256 一致；2026-11-19 在第二块同镜像香橙派完成资产核对、依赖安装、麦克风采音、录音推理、中心设备认证和服务自启检查。风险事件上传与网页试听仍待真实候选出现后验收。

## 当前流程

ESP32S3 USB 有线采音 → SenseVoice 转写/情绪 → RBT3 情绪融合分数 ≥ 0.715 → 微调 Qwen3.5-2B Q8_0 输出布尔风险判断 → 仅风险候选通过 HTTP POST 上传中心 → 网页人工复核。

当前硬件验证对象为香橙派 5 Pro 8 GB、Linux ARM64 / Ubuntu 22.04 / RK3588、同型号 XMOS+ESP32S3 麦克风。采音连接 ESP 的 USB，固件协议 RUSBAUT2。第二支麦克风已完成固件和现场讲话采集；新增麦克风仍需单独确认身份、刷写并绑定本机串口。

## 从这个仓库开始部署或继续开发

1. 请仓库所有者授予此**私人仓库及 Release** 读取权限；下载/克隆源码。三份模型附件在 [v0.1.0-migration Release](https://github.com/lyxnex/campus-risk-orangepi/releases/tag/v0.1.0-migration)，不在普通 Git 文件中。
2. 先按 [跨局域网 Tailscale 配置](docs/TAILSCALE_CROSS_NETWORK.md)让 Windows 中心和每块板加入同一现场 tailnet；若板端使用北邮校园网，先看 [校园网接入](docs/CAMPUS_NETWORK.md)。
3. 在新 Windows 电脑按 [中心与网页配置](docs/CENTER_WINDOWS.md)安装 Python 依赖、核验 CED 模型、生成本现场 token/网页密码和每块板的配对文件，打开本地网页。
4. 如需新增同型号麦克风，按 [固件与连接](docs/MIC_FIRMWARE.md)验证设备身份并配置 RUSBAUT2；已配置好的麦克风无需重刷。
5. 每块香橙派按 [新板导入步骤](docs/NEW_BOARD_IMPORT.md)下载两份板端附件、核对哈希、绑定自己的麦克风和配对文件、安装 systemd 服务。
6. 按 [开发说明](docs/DEVELOPMENT.md)继续添加功能；[逐文件说明](docs/FILE_MAP.md)可定位入口和依赖。
7. 安装完成后按 [日常开机与故障恢复](docs/DAILY_STARTUP.md)检查中心、Tailscale、麦克风和板端服务。

这套流程需要相同系统镜像、可用的 Python/Ubuntu 软件源、设备网络地址与现场独立凭据。仓库不会携带他人的 token、密码、Tailscale 身份或历史录音。当前发布不是无需现场输入的一键离线安装包。
**代码请取 `main` 分支；`v0.1.0-migration` 页面用于取得模型附件。**该预发行版的自动源码 ZIP 对应较早的标签，不含后来补齐的中心/固件文档。
克隆后可先运行 `py -3.11 .\tools\verify_repository.py`（Windows）或 `python3 tools/verify_repository.py`（Linux），核对运行源码与随仓库发布的固件文件。

## 目录

```text
edge/campus_risk_deploy/  已核验的板端 12 份运行模块/提示词
center/                  当前网页、接收端与本地密码设置工具
firmware/                ESP32S3 固件源码、四段已验证镜像、刷写工具与清单
config/                  无实际令牌、地址和麦克风身份的配置示例
deploy/                  systemd、Windows 中心启动/自启和受限防火墙脚本
tools/export_running_board.py  在旧板只读导出当前源码、依赖清单、模型校验信息
tools/package_board_assets.py  按已导出清单打包非 Qwen 模型与 ARM runtime，不复制录音或 Q8
tools/verify_assets.py   新板导入后逐文件核对 13 项模型与 runtime
tools/verify_repository.py 克隆后核对源码镜像与 ESP 固件
docs/MIGRATION.md        新板迁移的完整步骤和验收边界
docs/NEW_BOARD_IMPORT.md 同镜像新板的具体导入命令与独立配置步骤
docs/DAILY_STARTUP.md    已部署设备每次开机的检查与故障恢复
docs/GITHUB_UPLOAD.md    GitHub 提交范围和大文件方案
docs/CENTER_WINDOWS.md   新 Windows 电脑上的中心与网页配置
docs/TAILSCALE_CROSS_NETWORK.md 跨局域网 Tailscale 注册、换账号与配对
docs/CAMPUS_NETWORK.md 北邮校园网 Portal 接入与可选重认证
docs/MIC_FIRMWARE.md     新麦克风固件和 ESP USB 连接
docs/FILE_MAP.md         每个仓库文件的职责
docs/DEVELOPMENT.md      后续开发入口与验证方法
requirements/README.md   旧板实际依赖导出位置及安装限制
requirements/edge.lock.txt 旧板 Python 3.10.12 环境的包版本快照
requirements/center-ced.txt Windows 中心 CED-mini 直接依赖
assets.lock.json          旧板 13 项模型、tokenizer、ARM runtime 资产的大小和 SHA256
release_assets.json       私人 Release 三个附件的大小和 SHA256
model_manifests/ced-mini.json 中心 CED-mini 模型附件与内部文件校验
docs/board-snapshot.json  旧板只读导出的源码哈希、平台和 runtime 动态库信息
source_manifest.json     当前整理源码的 SHA256 和已核验版本
```

`capture/auto_inference.py` 包含被新引擎继承的方法，尽管仍有旧模型相关类定义，也不能删除。当前服务使用 `with_macbert=false`，不加载旧 MacBERT。

## 新板还需要哪些内容

`git clone` 只能得到程序。还需要下载模型及 ARM runtime、重建匹配的 Python 环境、导入本现场的私密配对配置、绑定实际 ESP USB 串口并安装 systemd 服务。详见 [新板导入命令](docs/NEW_BOARD_IMPORT.md)和[迁移验收步骤](docs/MIGRATION.md)。

模型与 runtime 已放入[私人预发行版 v0.1.0-migration](https://github.com/lyxnex/campus-risk-orangepi/releases/tag/v0.1.0-migration)：板端非 Qwen 资产、当前 Q8 GGUF、中心 CED-mini 共三个附件。大小和 SHA256 见 `release_assets.json`；不能把 1.9 GB GGUF 直接普通 Git 提交。当前 Q8：

- 文件：`qwen35_2b_finetuned-Q8_0.gguf`
- 字节数：`2012012128`
- SHA256：`415e7a97c4b68ab42e6494d4c17bef887217632e644a206ee61408f2e0bcca98`
- llama.cpp commit：`aa75ff3239f9cc675b081697deda9e1b112345c6`

保留 2 线程、2048 上下文和已有温度/内存保护。原始提示词、融合公式、阈值和模型哈希应保持一致。

## 数据和凭据

正式 `deployment_config.json`、中心上传令牌、网页密码哈希、Tailscale 状态、SSH 身份、历史录音、待传包和 `.venv` 均不提交 Git。示例文件不能原样作为生产配对文件使用。

每台新设备需独立设备编号、实际麦克风串口和新状态目录；不要克隆旧板的身份和录音队列。新现场使用新的中心配对凭据。

正式部署中 Windows 中心与多台香橙派可以位于不同局域网，项目通信使用同一现场 tailnet 内的 Tailscale 地址。中心地址和各板地址需在现场读取后填入私密配置及防火墙；人员使用各自获授权的账号，不沿用项目作者的 Tailscale 登录身份。

## CED-mini 辅助模型

CED-mini 保留在中心电脑上，源码为 `center/capture/ced_observer.py`，资产校验清单为 `model_manifests/ced-mini.json`，中心直接依赖版本为 `requirements/center-ced.txt`。独立模型附件 `ced-mini-assets.zip` 已上传至同一私人 Release，按清单校验后安装。

CED 对收到的风险候选录音补算 Speech、Music、Shout 等声学事件分数，用于人工复核辅助，不改变 RBT3→Qwen 的风险门控。旧板删除的是未使用的 CED 副本，当前电脑原模型、配置和已有分析结果均保留。

模型 ONNX 大小为 10,890,069 字节，SHA256 为 `2a86374dbe1fa03e96b0acf6a02618ac71852d12aa3d72a030b062ff1e5c5434`。附件四份模型文件均按清单核验。`tools/install_ced_assets.py` 将其放到 `assets/orangepi_ced/ced-mini`；新现场配置放在 `private/center_config.json` 时，生成器会正确写入相对模型路径。

中心环境需要 numpy 与 onnxruntime；上述依赖版本来自当前 Windows Python 3.11，不是新 ARM64 板端的依赖版本。原12MB Windows离线中心原型尚未集成CED及这两项额外依赖，不能把那个旧原型当作完整保留CED的交付包。
