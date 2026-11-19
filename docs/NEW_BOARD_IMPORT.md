# 全新香橙派 5 Pro 导入步骤

适用：同型号 8 GB 香橙派 5 Pro、同一 `Orangepi5pro_1.0.6_ubuntu_jammy_server_linux5.10.160.img` 镜像、独立 ESP32S3 麦克风。板端与 Windows 中心可处于不同局域网，须先按 [TAILSCALE_CROSS_NETWORK.md](TAILSCALE_CROSS_NETWORK.md)加入同一现场 tailnet；北邮校园网接入见 [CAMPUS_NETWORK.md](CAMPUS_NETWORK.md)。旧板无需停止服务或搬走模型、录音、队列。2026-11-19 已在第二块新板实测资产核对、依赖安装、麦克风采集、推理初始化和中心设备认证。

## 取得仓库和附件

仓库 `lyxnex/campus-risk-orangepi` 为私人仓库。安装人员需获得仓库和 Release 的读取权限；可在新板配置自己的只读 GitHub SSH 凭据，或在已登录 GitHub 的 Windows 电脑克隆后传源码归档。不要复制旧板 SSH 私钥、Tailscale 状态或设备身份。中心和私密配对文件应先按 [CENTER_WINDOWS.md](CENTER_WINDOWS.md)准备；新增麦克风按 [MIC_FIRMWARE.md](MIC_FIRMWARE.md)配置。

从[私人预发行版 `v0.1.0-migration`](https://github.com/lyxnex/campus-risk-orangepi/releases/tag/v0.1.0-migration)取得两个附件：`nonqwen-assets.tar` 和 `qwen35_2b_finetuned-Q8_0.gguf`。可在已登录 GitHub 的电脑下载，再通过 `scp` 传入新板 `/home/orangepi/campus-risk/downloads/`。传输时不要把 GitHub token 放在 URL、命令历史或仓库里。

在**新板**执行（完成 GitHub SSH 读取授权后）：

```bash
sudo apt update
sudo apt install -y git python3-venv python3-pip libgomp1 libssl3
mkdir -p /home/orangepi/campus-risk/downloads
cd /home/orangepi/campus-risk
git clone git@github.com:lyxnex/campus-risk-orangepi.git source
```

若新板没有 GitHub SSH 凭据，在 Windows PowerShell 的本地仓库目录执行 `git archive --format=tar --output=source.tar HEAD`，用 `scp .\source.tar orangepi@<新板IP>:/home/orangepi/campus-risk/downloads/` 传入，随后在**新板 SSH** 中运行 `mkdir -p /home/orangepi/campus-risk/source && tar -xf /home/orangepi/campus-risk/downloads/source.tar -C /home/orangepi/campus-risk/source`。传输前后分别计算 SHA256 并核对。Windows `scp` 命令不能在 `orangepi@...$` 中执行。

确认两个附件都已放进 `downloads/` 后，先核对整包 SHA256：

```bash
cd /home/orangepi/campus-risk
echo 'e93b74e9f6a7c2cc2ce33fd91a5bd21b0823360ee62d36723919f7b20c248560  downloads/nonqwen-assets.tar' | sha256sum -c -
echo '415e7a97c4b68ab42e6494d4c17bef887217632e644a206ee61408f2e0bcca98  downloads/qwen35_2b_finetuned-Q8_0.gguf' | sha256sum -c -
tar -xf downloads/nonqwen-assets.tar -C .
mkdir -p qwen35_cpu_test/clean_v2_20261117_102215
cp downloads/qwen35_2b_finetuned-Q8_0.gguf qwen35_cpu_test/clean_v2_20261117_102215/
python3 source/tools/verify_assets.py --base /home/orangepi/campus-risk --lock source/assets.lock.json
chmod +x qwen35_cpu_test/runtime/llama.cpp-aa75ff323/build-arm64/bin/llama-server
ldd qwen35_cpu_test/runtime/llama.cpp-aa75ff323/build-arm64/bin/llama-server
```

只有看到 `ASSETS_VERIFIED files=13` 且 `ldd` 没有 `not found` 才继续。模型和运行库的目标路径由清单固定；新设备身份、串口和中心地址需分别设置。

## 重新建立 Python 环境和部署代码

```bash
cd /home/orangepi/campus-risk
python3 -m venv .venv
.venv/bin/python -m pip install -r source/requirements/edge.lock.txt --extra-index-url https://download.pytorch.org/whl/cpu
.venv/bin/python -c 'import numpy, sherpa_onnx, torch, transformers, serial; print("PYTHON_IMPORT_OK")'
mkdir -p qwen35_clean_v2_deploy_20261117
cp -a source/edge/campus_risk_deploy qwen35_clean_v2_deploy_20261117/
```

`edge.lock.txt` 是旧板已安装环境的版本快照。新板实测普通 PyPI 不提供 `torch==2.9.1+cpu`，因此安装命令加入 PyTorch 官方 CPU 软件源。若其他 ARM64 wheel 无法取得，应先从可信供应源取得匹配 Python 3.10/aarch64 的包，并验证实际导入和推理，不能复制旧板 `.venv` 代替安装。

在新板执行 `ls -l /dev/serial/by-id/`，把本板实际 ESP USB 串口填入中心生成的 `private/pairing/<device-id>.json` 的 `port`。在 Windows PowerShell 用 `scp` 将该文件传到新板 `downloads/`，再在新板运行：

```bash
cd /home/orangepi/campus-risk
mkdir -p qwen35_clean_v2_deploy_20261117
install -m 600 downloads/<device-id>.json qwen35_clean_v2_deploy_20261117/deployment_config.json
rm downloads/<device-id>.json
```

将 `<device-id>` 替换为该板编号。核对私密配置中每台板唯一的 `device_id`、`scene_id`、`scene_name`、`state_dir`，以及本现场的 `center_url`、token 和串口。保持策略 `rbt3_fusion0715_then_qwen35_2b_clean_v2_q8`、阈值 `0.715`、Qwen 2 线程、2048 上下文和原有温度/内存保护。不要提交该配置，也不要复制旧板的录音、队列或 ESP 全 flash 身份。

## 接入中心并安装服务

先在中心的私密配置里登记新 `device_id`，保留正式数据目录和 `recording_epoch`；确认中心地址、网络和新 ESP 的 `RUSBAUT2` 固件。确认配置有效后，在**新板**安装模板服务：

```bash
sudo install -m 644 source/deploy/campus-risk-qwen35-clean-v2.service /etc/systemd/system/campus-risk-qwen35-clean-v2.service
sudo systemctl daemon-reload
sudo systemctl enable --now campus-risk-qwen35-clean-v2.service
systemctl show campus-risk-qwen35-clean-v2.service -p ActiveState -p SubState
```

服务模板使用 `orangepi` 用户、CPU 亲和 4/5 和固定目录；若新板用户名或安装目录不同，先调整模板。最终需在新板实测 `capture=armed`、`inference=ready`、中心收到风险候选 ACK、网页音频可播放、断网重传和开机恢复；仅安装成功不算端到端验收。

第二块新板实测达到 `active/running`、`capture=armed`、`inference=ready`，一次现场讲话产生录音并完成推理，中心 `/api/device-check` 使用新板凭据返回成功。该次普通讲话未生成风险候选，故风险候选上传 ACK、网页音频、断网重传和重启恢复仍需分别实测。
