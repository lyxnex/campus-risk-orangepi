# 全新香橙派 5 Pro 导入步骤

适用：同型号 8 GB 香橙派 5 Pro、同一 `Orangepi5pro_1.0.6_ubuntu_jammy_server_linux5.10.160.img` 镜像、独立 ESP32S3 麦克风。旧板无需停止服务或搬走模型、录音、队列。本步骤尚待新板实机验收。

## 取得仓库和附件

仓库 `lyxnex/campus-risk-orangepi` 为私人仓库。每位安装人员需获得仓库读取权限，并在新板配置自己的只读 SSH 凭据；不要复制旧板 SSH 私钥、Tailscale 状态或设备身份。克隆后应核对仓库的 `assets.lock.json`。

从私人 Release `v0.1.0-migration` 取得两个附件：`nonqwen-assets.tar` 和 `qwen35_2b_finetuned-Q8_0.gguf`。可在已登录 GitHub 的电脑下载，再通过 `scp` 传入新板 `/home/orangepi/campus-risk/downloads/`。传输时不要把 GitHub token 放在 URL、命令历史或仓库里。

在**新板**执行（完成 GitHub SSH 读取授权后）：

```bash
sudo apt update
sudo apt install -y git python3-venv python3-pip libgomp1 libssl3
mkdir -p /home/orangepi/campus-risk/downloads
cd /home/orangepi/campus-risk
git clone git@github.com:lyxnex/campus-risk-orangepi.git source
```

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
.venv/bin/python -m pip install -r source/requirements/edge.lock.txt
.venv/bin/python -c 'import numpy, sherpa_onnx, torch, transformers, serial; print("PYTHON_IMPORT_OK")'
mkdir -p qwen35_clean_v2_deploy_20261117
cp -a source/edge/campus_risk_deploy qwen35_clean_v2_deploy_20261117/
install -m 600 source/config/edge.example.json qwen35_clean_v2_deploy_20261117/deployment_config.json
```

`edge.lock.txt` 是旧板已安装环境的版本快照；若 `torch==2.9.1+cpu` 或其他 ARM64 wheel 无法取得，应先从可信供应源取得匹配 Python 3.10/aarch64 的包，并验证实际导入和推理，不能复制旧板 `.venv` 代替安装。

编辑新板私密 `deployment_config.json`：为每台板指定唯一 `device_id`、`scene_id`、`scene_name`、新 `state_dir` 和本板 `/dev/serial/by-id/` 中的实际 ESP USB 串口；填写本现场的 `center_url` 与共享上传 token。保持策略 `rbt3_fusion0715_then_qwen35_2b_clean_v2_q8`、阈值 `0.715`、Qwen 2 线程、2048 上下文和原有温度/内存保护。不要提交该配置，也不要复制旧板的录音、队列或 ESP 全 flash 身份。

## 接入中心并安装服务

先在中心的私密配置里登记新 `device_id`，保留正式数据目录和 `recording_epoch`；确认中心地址、网络和新 ESP 的 `RUSBAUT2` 固件。确认配置有效后，在**新板**安装模板服务：

```bash
sudo install -m 644 source/deploy/campus-risk-qwen35-clean-v2.service /etc/systemd/system/campus-risk-qwen35-clean-v2.service
sudo systemctl daemon-reload
sudo systemctl enable --now campus-risk-qwen35-clean-v2.service
systemctl show campus-risk-qwen35-clean-v2.service -p ActiveState -p SubState
```

服务模板使用 `orangepi` 用户、CPU 亲和 4/5 和固定目录；若新板用户名或安装目录不同，先调整模板。最终需在新板实测 `capture=armed`、`inference=ready`、中心收到风险候选 ACK、网页音频可播放、断网重传和开机恢复；仅安装成功不算端到端验收。
