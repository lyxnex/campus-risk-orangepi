# 给后续开发者的交接说明

## 当前运行链路

```text
ESP32S3 RUSBAUT2 → capture.py 保存 15 秒双声道 WAV
  → SenseVoice ASR/情绪 → RBT3 融合分数（阈值 0.715）
  → 仅过门控时由 Qwen3.5-2B Q8_0 作布尔复核
  → 两级均通过才打包风险候选 → center.py 校验并保存
  → CED-mini 对中心已收到的录音补算声学事件 → dashboard.html 人工复核
```

`edge_service.py` 分别监督采集、推理、上传，录音先留在板端，候选包在 outbox 等待中心 ACK。`center.py` 不信任上传的任意文件：它核对事件模式、哈希、策略、时间范围和 WAV 格式后才落盘。网页会话密码与设备上传 token 是两种独立凭据。完整文件职责见 [FILE_MAP.md](FILE_MAP.md)。

## 关键不变量

- 正在运行的策略为 `rbt3_fusion0715_then_qwen35_2b_clean_v2_q8`，RBT3 融合门槛为 **0.715**。Qwen 输出布尔判断，不提供校准概率；网页不得把它显示成风险百分比。
- 当前 Q8 模型 SHA256 是 `415e7a97c4b68ab42e6494d4c17bef887217632e644a206ee61408f2e0bcca98`，提示词和 ARM runtime 也在资产/源码清单中。变更模型、提示词、融合公式或门槛时要更新相应清单并重新验证现场效果，不能把旧模型指标当作新门槛成绩。
- Qwen 2 线程、上下文 2048、CPU 亲和 4/5、温度与内存保护来自散热片条件下的现场部署。修改并发与散热策略前先看资源日志。
- CED-mini 只分析已收到的录音，用于辅助人工复核，不参与两级风险判决。`ced_mini.json` 不存在时，老事件仍应能显示与复核。
- 每块板使用自己的 `device_id`、`scene_id`、ESP 持久串口和 `state_dir`。同一现场现采用**共享上传 token + 设备允许列表**，不是每板独立 token。增加板时需同时更新中心允许列表及网络规则。
- 板端与中心可能不在同一局域网，正式链路使用同一现场 tailnet 内的 Tailscale 地址。人员使用各自获授权的账号；账号交接与校园网恢复见 `TAILSCALE_CROSS_NETWORK.md` 和 `CAMPUS_NETWORK.md`。

## 修改哪里

| 需求 | 主要改动位置 | 同时核对 |
| --- | --- | --- |
| ESP 采样、VAD、重传 | `firmware/esp32s3/xiao_usb_arq.ino`、`edge/.../capture/` | 协议版本、CRC、15 秒/2 秒重叠、固件清单、真实录音 |
| ASR / RBT3 / Qwen 判决 | `edge/.../capture/auto_inference.py`、`qwen_inference.py`、`decision.py` | 中心 `decision.py`、模型清单、策略名称、误报/漏报评估 |
| 上传包或中心校验 | `edge/.../common.py`、`edge_service.py`、`center/common.py`、`center.py` | 兼容性、旧板待传包、重复上传与断网重传 |
| 网页外观与复核交互 | `center/dashboard.html` | `center.py` API、登录、录音试听、人工状态持久化 |
| CED 辅助观察 | `center/capture/ced_observer.py` | `model_manifests/ced-mini.json`、Windows 依赖、网页说明 |
| 新设备/新现场安装 | `tools/init_site.py`、`config/`、`deploy/`、部署文档 | 私密配置不进 Git、中心/板端设备编号一致 |

板端的 `capture/auto_inference.py` 包含 Qwen 引擎仍在继承的方法；不能因为其中出现旧模型类就直接删除整个文件。中心与板端的 `common.py`、`decision.py` 保持相同规则，修改协议应考虑已在旧板 outbox 的候选包。

## 本地检查和现场验收

提交前运行 `python -m py_compile` 检查修改过的 Python 文件，确认 `git status --short` 不含 `private/`、模型原文件、录音、队列和备份。板端资产用 `python3 source/tools/verify_assets.py --base /home/orangepi/campus-risk --lock source/assets.lock.json` 核对；中心 CED 用 `tools/install_ced_assets.py` 核对。真实新板曾达到 `active/running`、`capture=armed`、`inference=ready`，现场讲话产生录音并被处理，中心设备认证成功。

风险事件的上传 ACK、网页试听、CED 输出、断网重传与断电/重启恢复尚需真实事件及现场维护窗口验收。普通讲话被本地判为非风险时网页不出现事件，这是当前设计；不要人为伪造正式证据来声称上传链路已通过。若开发者需要自动化集成测试，应使用隔离的临时配置和数据目录，不能清空正式中心或旧板录音。

## 仓库与大文件

私人 Release `v0.1.0-migration` 提供两个板端附件及一个中心 CED 附件，大小与 SHA256 在 `release_assets.json`。新同型号板已经使用前两个附件实测导入；新增模型时应发布独立版本并记录文件来源、哈希、兼容平台及迁移方式。正式中心配置、网页密码哈希、上传 token、ESP 全 flash 备份、历史录音与待传包均不在仓库。
