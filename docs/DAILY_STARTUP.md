# 日常开机、运行检查与故障恢复

适用于已按 [新板导入指南](NEW_BOARD_IMPORT.md)安装模型、私密配置和 `campus-risk-qwen35-clean-v2.service` 的香橙派 5 Pro。首次安装才需要建虚拟环境、复制模型和安装服务；**日常开机不需要重新执行这些步骤，也不需要手动运行 `edge_service.py`**。每块板保留自己的配置、状态目录、录音和待传包。

## 每次开机的顺序

1. 给麦克风、ESP32S3 和香橙派供电，确认接的是 **ESP USB → 香橙派**。让香橙派接入能上网的网络。北邮校园网 Portal 到期时先按 [校园网指南](CAMPUS_NETWORK.md)恢复认证。
2. 启动 Windows 中心电脑，确认 Tailscale 仍连在本现场的 tailnet，再检查中心健康接口。中心可能使用仓库的计划任务自动启动，也可能由操作员手动启动，见下文。
3. 香橙派的 `tailscaled` 与项目 systemd 服务应随开机自行运行。用 SSH 连上板端检查状态；若中心稍晚启动，板端会继续本地采集/推理，并重试待传的风险候选。
4. 在中心电脑浏览器打开 <http://127.0.0.1:8765/>，登录网页查看已上传的风险候选。普通讲话即使录音并完成推理，也不会自动出现在网页。

### 中心电脑：先查健康，再决定是否启动

在 **Windows PowerShell** 执行：

```powershell
$Ts = 'C:\Program Files\Tailscale\tailscale.exe'
& $Ts status
& $Ts ip -4
Invoke-RestMethod -Uri 'http://127.0.0.1:8765/healthz' -TimeoutSec 5
```

健康接口应返回 `ok: true`。已经正常运行就不要再开第二个中心进程。若中心是按 [Windows 中心指南](CENTER_WINDOWS.md)安装的开机计划任务，可检查 `Get-ScheduledTask -TaskName CampusRiskCenter-GitHub -ErrorAction SilentlyContinue`；任务已存在而中心未运行时，用有权限的 PowerShell 执行 `Start-ScheduledTask -TaskName CampusRiskCenter-GitHub`，随后重查健康接口。未安装该任务的新现场，在仓库根目录运行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deploy\start_center_windows.ps1`，并保持该窗口打开。已有其他正式中心的现场应使用其原有启动方式，保留原配置和数据；**不要为了日常开机重新运行 `init_site.py`**。

### 香橙派：确认自动启动

以下命令在**每块香橙派自己的 SSH 终端**执行：

```bash
systemctl is-enabled tailscaled
systemctl is-active tailscaled
tailscale status
systemctl is-enabled campus-risk-qwen35-clean-v2.service
systemctl show campus-risk-qwen35-clean-v2.service -p ActiveState -p SubState
ls -l /dev/serial/by-id/
```

项目服务预期是 `enabled`、`ActiveState=active`、`SubState=running`。`tailscale status` 应显示本机和中心在同一个 tailnet；麦克风 ESP 的持久串口路径应存在。安装时若忘记启用服务，**只需补做一次** `sudo systemctl enable --now campus-risk-qwen35-clean-v2.service`。之后每次开机只检查，不用重复启动。

模型加载需要时间；刚开机看到 `inference=loading_models` 或 `capture=connecting` 时，先等待状态稳定。用下面命令读取本板私密配置指定的状态目录，输出不含 token：

```bash
python3 - <<'PY'
import json
from pathlib import Path
config = Path('/home/orangepi/campus-risk/qwen35_clean_v2_deploy_20261117/deployment_config.json')
c = json.loads(config.read_text())
status = Path(c['state_dir']) / 'status.json'
if not status.exists():
    raise SystemExit(f'尚未生成状态文件：{status}；先检查 systemd 日志')
s = json.loads(status.read_text())
keys = ('device_id', 'service', 'capture', 'inference', 'upload',
        'pending_uploads', 'disk_free_gib', 'time_sync', 'updated_at', 'thread_health')
print(json.dumps({k: s.get(k) for k in keys}, ensure_ascii=False, indent=2))
PY
```

正常稳定状态应看到 `service=running`、`capture=armed`、`inference=ready`，并且 `thread_health` 中三个工作线程均为 `true`。没有待传事件时，`upload=starting` 可能只是空闲，不能仅凭这个字段判定上传故障；结合 `pending_uploads`、日志和中心连接判断。模型正在处理一段录音时，`inference=processing` 也是暂时状态。`updated_at` 应持续更新。

### 核对板端到中心的通路

从本板配置读取中心地址，避免照抄另一现场的 Tailscale IP：

```bash
CENTER_URL=$(python3 -c 'import json; print(json.load(open("/home/orangepi/campus-risk/qwen35_clean_v2_deploy_20261117/deployment_config.json"))["center_url"])')
curl -fsS --max-time 10 "$CENTER_URL/healthz"
```

这里的健康接口不需要 token；它只能证明中心可达，不能单独证明该设备的上传认证和真实事件已验收。风险候选出现时，还需在板端日志中看到 `UPLOAD_ACK`，并在中心网页看到对应事件。普通讲话可用于核对本地采音和推理，但不应人为改写正式事件来制造验收结果。

## 异常时按现象处理

| 现象 | 先检查 | 处理 |
| --- | --- | --- |
| SSH 连不上板端 | 板端是否开机、底层网络/校园网 Portal 是否已认证、`tailscale status`、`systemctl is-active ssh` | 先在板端本地恢复网络，再检查 Tailscale；不要在仅有 Tailscale SSH 的连接中执行 `tailscale logout`。 |
| 项目服务不是 `active/running` | `sudo journalctl -u campus-risk-qwen35-clean-v2.service -b -n 80 --no-pager` | 确认模型/配置路径后运行 `sudo systemctl start campus-risk-qwen35-clean-v2.service`；仍失败时按日志定位。 |
| `capture=connecting` 或 `reconnecting` 持续不变 | ESP USB 电源/线缆、`ls -l /dev/serial/by-id/`、本板配置的 `port` | 恢复连接并核对持久路径；采音线程会重试，必要时再重启项目服务。 |
| `inference=model_error_retrying` 持续出现 | 项目日志、模型文件、磁盘空间和内存 | 先修复具体报错，不要重新初始化或删除录音。 |
| 中心健康接口不可达、`upload=retry_wait` | Windows 中心、两端 Tailscale、中心地址和防火墙 | 恢复通路后上传线程自动重试；保留板端 `outbox` 及 `.retry.json`。 |
| `capture=paused_low_disk` | `df -h /home`、状态中的 `disk_free_gib` | 按现场保留策略安全释放空间；不要直接清空历史录音、状态目录或待传包。 |

查看本次开机日志：

```bash
sudo journalctl -u campus-risk-qwen35-clean-v2.service -b -n 80 --no-pager
```

确认确需重新启动时执行 `sudo systemctl restart campus-risk-qwen35-clean-v2.service`，然后重新核对状态。重启会中断当前采音或推理，宜在合适的维护时段操作。不要同时手工启动第二份 `edge_service.py`：同一状态目录有进程锁，重复运行也会争用麦克风。

## 不属于每日开机的操作

不必再次 `git clone`、下载模型、安装 Python 包、刷麦克风固件、运行 `init_site.py`、修改 0.715 阈值或重新配对。只有更换设备、麦克风、tailnet/中心地址、代码或系统后，才按对应安装指南更新，并在变更后做一次完整验收。中心或板端重启恢复、断网重传和真实风险候选上传，应在维护窗口实测；此前仅完成的功能不能据此视为已经通过这些验收。
