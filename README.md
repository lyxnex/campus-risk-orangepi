# 香橙派校园风险语言检测

这是从已运行版本整理并上传到私人 GitHub 仓库的源码。2026-11-18 已对旧板做只读导出，12 份板端源码与本目录逐文件 SHA256 一致；尚未在全新香橙派上完成安装验收。代码和模型不再训练、合并或转换。

## 当前流程

ESP32S3 USB 有线采音 → SenseVoice 转写/情绪 → RBT3 情绪融合分数 ≥ 0.715 → 微调 Qwen3.5-2B Q8_0 输出布尔风险判断 → 仅风险候选通过 HTTP POST 上传中心 → 网页人工复核。

当前硬件验证对象为香橙派 5 Pro 8 GB、Linux ARM64 / Ubuntu 22.04 / RK3588、同型号 XMOS+ESP32S3 麦克风。采音连接 ESP 的 USB，固件协议 RUSBAUT2。新麦克风尚需各自配置，不能只复制香橙派文件就默认可用。

## 目录

```text
edge/campus_risk_deploy/  已核验的板端 12 份运行模块/提示词
center/                  当前网页、接收端与本地密码设置工具
config/                  无实际令牌、地址和麦克风身份的配置示例
deploy/                  systemd 服务模板
tools/export_running_board.py  在旧板只读导出当前源码、依赖清单、模型校验信息
tools/package_board_assets.py  按已导出清单打包非 Qwen 模型与 ARM runtime，不复制录音或 Q8
tools/verify_assets.py   新板导入后逐文件核对 13 项模型与 runtime
docs/MIGRATION.md        新板迁移的完整步骤和验收边界
docs/NEW_BOARD_IMPORT.md 同镜像新板的具体导入命令与独立配置步骤
docs/GITHUB_UPLOAD.md    GitHub 提交范围和大文件方案
requirements/README.md   旧板实际依赖导出位置及安装限制
requirements/edge.lock.txt 旧板 Python 3.10.12 环境的包版本快照
assets.lock.json          旧板 13 项模型、tokenizer、ARM runtime 资产的大小和 SHA256
release_assets.json       私人 Release 两个附件的大小和 SHA256
docs/board-snapshot.json  旧板只读导出的源码哈希、平台和 runtime 动态库信息
source_manifest.json     当前整理源码的 SHA256 和已核验版本
```

`capture/auto_inference.py` 包含被新引擎继承的方法，尽管仍有旧模型相关类定义，也不能删除。当前服务使用 `with_macbert=false`，不加载旧 MacBERT。

## 新板还需要哪些内容

`git clone` 只能得到程序。还需要下载模型及 ARM runtime、重建匹配的 Python 环境、导入本现场的私密配对配置、绑定实际 ESP USB 串口并安装 systemd 服务。详见 [新板导入命令](docs/NEW_BOARD_IMPORT.md)和[迁移验收步骤](docs/MIGRATION.md)。

模型与 runtime 已放入[私人预发行版 v0.1.0-migration](https://github.com/lyxnex/campus-risk-orangepi/releases/tag/v0.1.0-migration)的两个附件，大小和 SHA256 见 `release_assets.json`；不能把 1.9 GB GGUF 直接普通 Git 提交。当前 Q8：

- 文件：`qwen35_2b_finetuned-Q8_0.gguf`
- 字节数：`2012012128`
- SHA256：`415e7a97c4b68ab42e6494d4c17bef887217632e644a206ee61408f2e0bcca98`
- llama.cpp commit：`aa75ff3239f9cc675b081697deda9e1b112345c6`

保留 2 线程、2048 上下文和已有温度/内存保护。原始提示词、融合公式、阈值和模型哈希应保持一致。

## 数据和凭据

正式 `deployment_config.json`、中心上传令牌、网页密码哈希、Tailscale 状态、SSH 身份、历史录音、待传包和 `.venv` 均不提交 Git。示例文件不能原样作为生产配对文件使用。

每台新设备需独立设备编号、实际麦克风串口和新状态目录；不要克隆旧板的身份和录音队列。新现场使用新的中心配对凭据。

当前正式交付方向是甲方现场常开 Windows 中心电脑与多台香橙派通过局域网连接，中心地址由现场保留。Tailscale 为可选网络/运维通道，具体地址和账号不写死到源码。
