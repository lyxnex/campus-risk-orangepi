# 在一台新的 Windows 电脑部署中心与网页

本页针对 Windows x64、Python 3.11、能访问 GitHub 和 Python 包源的新电脑。中心接收跨局域网香橙派的风险候选，保存录音与元数据，并提供本机网页复核。先按 [TAILSCALE_CROSS_NETWORK.md](TAILSCALE_CROSS_NETWORK.md)将中心和板端加入同一个现场 tailnet。板端安装见 [NEW_BOARD_IMPORT.md](NEW_BOARD_IMPORT.md)，麦克风固件见 [MIC_FIRMWARE.md](MIC_FIRMWARE.md)。**不要在已有正式中心电脑上运行首次初始化命令**；已有中心的 `center_config.json`、`center_data` 和 `recording_epoch.json` 必须保留。

## 1. 取得代码和 CED 模型

先请仓库所有者授予 `lyxnex/campus-risk-orangepi` 私人仓库及 Release 的读取权限。在新电脑的 PowerShell 中：

```powershell
git clone https://github.com/lyxnex/campus-risk-orangepi.git
cd .\campus-risk-orangepi
py -3.11 --version
```

从 [v0.1.0-migration 的 Release](https://github.com/lyxnex/campus-risk-orangepi/releases/tag/v0.1.0-migration) 下载 `ced-mini-assets.zip` 到仓库下新建的 `downloads` 目录。它是中心辅助声学模型；网页和服务端源码已在 `center/`。下载后运行：

```powershell
py -3.11 .\tools\install_ced_assets.py --archive .\downloads\ced-mini-assets.zip
```

看到 `CED_ASSETS_VERIFIED files=4` 才继续。脚本先核对整个 ZIP 的 SHA256 与其中四份模型文件，再写入被 Git 忽略的 `assets/orangepi_ced/ced-mini/`。CED 只辅助复核，不改变板端 0.715 门控和 Qwen 判断。

## 2. 建立中心环境和私密配置

```powershell
py -3.11 -m venv .\private\.venv
& .\private\.venv\Scripts\python.exe -m pip install -r .\requirements\center-ced.txt
& .\private\.venv\Scripts\python.exe -c "import numpy, onnxruntime; print('CENTER_IMPORT_OK')"
$Ts = 'C:\Program Files\Tailscale\tailscale.exe'
$CenterTsIp = (& $Ts ip -4).Trim()
py -3.11 .\tools\init_site.py --center-host $CenterTsIp --device-id opi5pro-01 --device-id opi5pro-02
```

`$CenterTsIp` 必须是这台中心电脑在**现场 tailnet** 的 IPv4。按现场实际设备数重复 `--device-id`，不同板必须用不同编号。初始化时交互输入网页密码；脚本生成随机上传 token、密码哈希、`private/center_config.json` 和 `private/pairing/<device-id>.json`，不会把密码或 token 打印到终端。私密目录已被 `.gitignore` 排除，仍应只由授权人员保管。更换 Tailscale 账号/tailnet 后中心 IP 可能变化，需更新板端 `center_url` 和中心防火墙规则。

已配置过的现场不要重新初始化来“修复”问题：脚本拒绝覆盖已有配置；如需增加板，需保留现有 token、网页密码、数据目录及接收历史，并在中心 `allowed_devices` 增加独立编号后重启中心。

## 3. 启动中心并打开网页

在仓库根目录运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deploy\start_center_windows.ps1
```

此窗口保持打开。另开浏览器访问 <http://127.0.0.1:8765/>，用刚设置的网页密码登录；`Invoke-RestMethod http://127.0.0.1:8765/healthz` 应返回 `ok: true`。网页源码在 `center/dashboard.html`，入口是中心服务的 `/` 路由，无需另起前端开发服务器。

手动验收完成后，先在该窗口按 `Ctrl+C` 停止中心。若现场要求中心在 Windows 开机后自行运行，可在**管理员 PowerShell** 从仓库根目录执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deploy\install_center_autostart.ps1
```

脚本登记 `CampusRiskCenter-GitHub` 开机计划任务，以本机 SYSTEM 账户启动并检查健康接口；遇到同名任务或 8765 端口已占用会停止，不覆盖现有运行实例。执行后应再核对网页和 `Get-ScheduledTask -TaskName CampusRiskCenter-GitHub`。**全新电脑上的开机重启恢复尚未做现场实测**，交付时应安排一次维护窗口验证。

若板端通过网络访问中心，在**管理员 PowerShell** 中按实际 IPv4 建立限定规则，例如：

```powershell
$BoardTsIps = @('100.x.x.x', '100.y.y.y') # 改成两块板的实际 Tailscale IPv4
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deploy\allow_center_windows.ps1 -CenterAddress $CenterTsIp -BoardAddress $BoardTsIps
```

两个 `100.x.x.x` 是占位符，必须替换为**各板实际的 Tailscale IPv4**。脚本只允许列出的板 IP 到中心 Tailscale IP 的 TCP 8765，不会替换已有同名规则。所有设备应加入同一现场 tailnet，人员可使用各自获授权的账号；**不要复制旧设备的 Tailscale 状态或 SSH 私钥**。网页含登录与录音，只在受控网络内提供；经非可信网络访问网页时应配置 TLS 或可信 HTTPS 代理。

## 4. 将每块板与中心配对

`private/pairing/<device-id>.json` 是该板的私密配置。到新板运行 `ls -l /dev/serial/by-id/`，将该文件里的 `port` 替换为**本板 ESP USB 的实际持久路径**，并核对 `device_id`、`scene_id`、`scene_name`、`center_url`、`state_dir`。配置中的 `token` 必须与中心相同；不同板的 `device_id` 和 `state_dir` 必须不同。通过 `scp` 传到对应板，按[板端导入指南](NEW_BOARD_IMPORT.md)以 `0600` 权限安装，随后删除下载目录里的临时副本。

板端应能取得中心 `/healthz`，并使用自身配置中的 token 调用 `/api/device-check?device=<device-id>` 得到 `ok: true`。网页只列出通过两级判断的风险候选；普通录音只在板端本地处理，不会自动出现在网页。

## 5. 网页功能和数据位置

网页支持登录、按复核状态筛选、查看设备/场景与转写、试听 15 秒录音、下载证据文件、查看 RBT3/Qwen 和 CED-mini 结果、保存人工复核意见。`center/dashboard.html` 是页面及浏览器侧 JavaScript；`center/center.py` 提供 HTTP API、严格校验上传包并落盘；`center/web_auth.py` 负责网页会话和密码哈希。CED 后台处理逻辑在 `center/capture/ced_observer.py`。

首次安装的数据在 `private/center_data/`，其中 `events/` 是接收的候选和人工复核，`incoming/` 是接收中的暂存，`center.log` 是日志；若存在 `recording_epoch.json`，它决定旧录音上传的保留边界。迁移已有中心时应连同这些文件整体保留，不要用新初始化目录覆盖。

## 6. 验收与排查

1. 中心 `/healthz` 为 `ok: true`，网页能登录；新板 `/api/device-check` 通过。
2. 新板 `capture=armed`、`inference=ready`，现场普通讲话生成并处理录音。
3. 真实风险候选出现时，板端得到上传 ACK，中心 `events/` 增加对应设备事件，网页能试听、下载并保存复核结果；CED 生成 `ced_mini.json`。不应制造或改写正式事件来冒充验收。
4. 断网后候选应留在板端 outbox，恢复网络后重传；中心重启和板端重启后状态应恢复。此项须在现场另行实测。

本仓库提供可运行的源码安装路径，依赖 Python 包源；旧的 `customer_lan_center_windows_x64.zip` 离线原型未包含 CED 依赖，不能替代以上完整流程。
