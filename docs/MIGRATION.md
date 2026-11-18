# 新香橙派迁移顺序

本目录是整理后的源码，不是已经在新板验收的一键安装器。旧板继续运行，不移动或删除其模型、历史录音、待传队列及私密配置。

同镜像新板的可执行命令见 [NEW_BOARD_IMPORT.md](NEW_BOARD_IMPORT.md)。

1. 旧板已运行 `tools/export_running_board.py`：只读导出未停止服务，12 份实际代码与 `source_manifest.json` 一致；Python 版本、依赖和 13 项资产校验信息已纳入仓库。
2. 从确认的 GitHub 目标获取代码；模型附件单独发布，并逐文件对照 `assets.lock.json` 校验。
3. 新板准备匹配的 ARM64 Ubuntu/Python 和系统库，启用串口权限、时间同步、散热。已有系统可继续使用，不要求重新烧录系统；若与旧板系统不同，先做兼容性验证。
4. 克隆仓库。建立 `/home/orangepi/campus-risk`，下载并核验资产，恢复以下布局：

```text
/home/orangepi/campus-risk/
  .venv/                         在新板重新创建
  orangepi_cpu/                  SenseVoice ONNX、tokens、manifest
  orangepi_baseline/
    manifest.json                RBT3 融合系数
    rbt3_finetuned/               模型与 tokenizer
  qwen35_cpu_test/
    clean_v2_20261117_102215/qwen35_2b_finetuned-Q8_0.gguf
    runtime/llama.cpp-aa75ff323/build-arm64/bin/llama-server
  qwen35_clean_v2_deploy_20261117/
    campus_risk_deploy/           从仓库 edge/campus_risk_deploy 复制
    deployment_config.json       本现场私密配置，绝不提交 Git
  deployment_data_SITE_DEVICE/    每台新板的全新数据目录
```

5. 按旧板实际包清单建立 venv，验证 numpy/sherpa_onnx/torch/transformers/serial 导入、ARM runtime `ldd` 和可执行权限。当前 CPU 二进制依赖 Ubuntu 系统动态库，不能假定任意发行版直接通用。
6. 导入中心生成的该设备私密配对文件，填入模板中的 device_id、scene_id、scene_name、center_url、token、state_dir。不同现场更换凭据；同现场三个设备编号各自独立。保留原来的模型路径、Q8 SHA256 和 0.715 策略。
7. 用 `ls -l /dev/serial/by-id/` 确认实际 ESP USB，替换 port。新 ESP 麦克风需配置为相同 RUSBAUT2 固件；不复制旧 ESP 全 flash 备份来冒充新设备。
8. 先验证中心 `/healthz` 和带令牌的 `/api/device-check`，确认设备在中心允许列表。局域网保留中心地址；若使用 Tailscale，每台登录各自项目身份，不复制旧板 tailscaled 状态。
9. 对照新板用户和路径审查 `deploy/*.service` 后安装 systemd 服务；不能在旧板无条件覆盖已运行的服务。
10. 验收新板采音 armed、模型 ready、三线程健康、正常聊天本地处理、风险候选中心 ACK、网页音频播放、断网重传和开机恢复。只有通过新板实机验收后才标记可交付。

当前 Windows 中心离线安装原型独立存放在本地历史工作目录的 outputs/customer_lan_delivery，尚未做干净 Windows 的管理员安装/重启验收。这里的 center 源码仍可作为现有中心运行，不需要先安装那份原型。
