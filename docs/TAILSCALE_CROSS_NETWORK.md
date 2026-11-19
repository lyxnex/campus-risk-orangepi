# 跨局域网连接：Tailscale 与账号交接

本项目的正式拓扑是香橙派与 Windows 中心**可能处于不同局域网**。两端分别联网并加入同一个现场 Tailscale 网络（tailnet），板端用中心的 Tailscale IPv4 作为 `center_url`，中心防火墙只允许已登记板端的 Tailscale IPv4 访问 TCP 8765。维护电脑也加入该 tailnet 后，可通过普通 SSH 连接板端。北邮 `BUPT-portal` 的入网和断线重认证见 [CAMPUS_NETWORK.md](CAMPUS_NETWORK.md)。

## 1. 为现场建立独立身份

由现场负责人[注册并建立自己的 tailnet](https://tailscale.com/docs/how-to/quickstart)，或使用现场已经授权的 tailnet。**不同人员可以用自己的 Tailscale 账号加入同一个 tailnet，不需要共用某个人的登录账号**；负责人在管理控制台邀请成员并配置访问规则。中心电脑和每块板分别授权为独立设备，记录各自 `tailscale ip -4` 输出。不要复制旧板 `/var/lib/tailscale/`、Windows Tailscale 状态、SSH 私钥或个人认证链接。跨不同 tailnet 的共享设备功能只适合特定共享场景，本项目常驻双向通信优先放入同一个现场 tailnet。[Tailscale 邀请与设备共享说明](https://tailscale.com/docs/reference/inviting-vs-sharing)

## 2. Windows 中心加入现场 tailnet

在中心电脑按 [Tailscale Windows 安装说明](https://tailscale.com/docs/install/windows)安装客户端，使用现场授权的账号登录，并在管理控制台确认中心在线。PowerShell 查询：

```powershell
$Ts = 'C:\Program Files\Tailscale\tailscale.exe'
& $Ts status
& $Ts ip -4
```

记下中心的 Tailscale IPv4，**在 [CENTER_WINDOWS.md](CENTER_WINDOWS.md) 的 `init_site.py --center-host` 中使用该地址**，而非校园网或家庭路由器分配的地址。中心需要在 Windows 用户退出登录后仍可访问时，在 Tailscale 托盘图标的 `Preferences → Run unattended` 启用无人登录运行，并在一次重启/退出登录后从另一设备验证；Linux 板端的 `tailscaled` 默认作为系统服务运行。[Tailscale 无人登录运行说明](https://tailscale.com/docs/how-to/run-unattended)

## 3. 每块新香橙派加入同一 tailnet

先用有线、热点或校园网让板端能访问互联网。下面命令在**香橙派本地终端或当前可用的 SSH** 执行：

```bash
curl -4fL --connect-timeout 8 --max-time 60 https://tailscale.com/install.sh -o /tmp/tailscale-install.sh
sudo sh /tmp/tailscale-install.sh
sudo systemctl enable --now tailscaled
sudo tailscale up
tailscale status
tailscale ip -4
```

`sudo tailscale up` 会显示登录网址；在浏览器中用**现场 tailnet 授权账号**完成该板授权。每块板都要单独执行、记录自己的地址，不能照抄另一块板。若现场启用了设备审批，须在控制台批准新设备后再测连接。[Tailscale Linux 安装说明](https://tailscale.com/docs/install/linux)

本项目使用 Ubuntu 的 `openssh-server` 和 `ssh orangepi@<板端TailscaleIPv4>`，没有要求开启 Tailscale SSH 功能。首次 SSH 前核对主机密钥指纹；不要为了连接成功而关闭主机密钥检查。

## 4. 旧账号退出并改用现场账号

**先确保能够在设备本地操作，或有另一条不依赖当前 Tailscale 的连接。** 如果当前 SSH 正走 Tailscale，在板端退出账号会立即切断该会话。正在运行的旧生产板不因编写本指南而切换账号；交接时应在计划好的维护窗口执行，并保留原模型、录音、队列和正式中心数据。

在需要移交的香橙派本地终端：

```bash
tailscale status
sudo tailscale logout
sudo tailscale up
tailscale status
tailscale ip -4
```

`logout` 会退出并使原登录失效；`up` 重新给出授权网址。用新现场账号登录，确认选中**与中心相同的 tailnet**。若设备上已经缓存多个获准使用的账号，可先用 `tailscale switch --list` 查看；`tailscale switch <账号>` 切换时同样会断开当前跨 tailnet 会话。不要直接删除 `tailscaled.state` 来代替正常退出。[Tailscale CLI 参考](https://tailscale.com/docs/reference/tailscale-cli)

Windows 中心更换账号时也先保证能在电脑本机操作：从 Tailscale 托盘菜单退出当前账号，再登录现场授权账号；也可在本机 PowerShell 运行 `& 'C:\Program Files\Tailscale\tailscale.exe' logout`，然后 `& 'C:\Program Files\Tailscale\tailscale.exe' up` 并完成浏览器授权。若原账号开启了 `Run unattended`，先由启用它的 Windows 用户关闭该模式，再切换并由新账号重新启用。切换后重新记录中心和板端 Tailscale IP，更新中心防火墙规则、各板 `center_url`，再验收服务。**不能沿用旧个人账号的 `100.x.y.z` 地址作为新现场配置常量。**[Tailscale 无人登录模式与账号所有权](https://tailscale.com/docs/how-to/run-unattended)

## 5. 配对和跨网验收

在 Windows 中心取得本机 Tailscale IPv4，在各板取得其 Tailscale IPv4。首次初始化示例（在仓库根目录 PowerShell）：

```powershell
$Ts = 'C:\Program Files\Tailscale\tailscale.exe'
$CenterTsIp = (& $Ts ip -4).Trim()
py -3.11 .\tools\init_site.py --center-host $CenterTsIp --device-id opi5pro-01 --device-id opi5pro-02
```

在管理员 PowerShell，按实际板端地址开放中心端口；[中心安装指南](CENTER_WINDOWS.md)有完整前后步骤：

```powershell
$BoardTsIps = @('100.x.x.x', '100.y.y.y') # 改成两块板的实际 Tailscale IPv4
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deploy\allow_center_windows.ps1 -CenterAddress $CenterTsIp -BoardAddress $BoardTsIps
```

上面的两个 `100.x.x.x` **仅为占位符，运行前必须换成实际地址**。若中心已有生产防火墙规则，先核对旧规则并有计划地增加新地址；脚本遇到同名规则会拒绝覆盖。配对文件中的 `center_url` 应为 `http://<中心TailscaleIPv4>:8765`，中心 `allowed_devices` 应包含各板唯一 `device_id`。

依次确认：

1. Windows 到板端：`& $Ts ping <板端TailscaleIPv4>`、`Test-NetConnection <板端TailscaleIPv4> -Port 22`，再 `ssh orangepi@<板端TailscaleIPv4>`。
2. 板端到中心：`tailscale ping <中心TailscaleIPv4>`，`curl -fsS http://<中心TailscaleIPv4>:8765/healthz`。
3. 板端用自己的私密配置调用 `/api/device-check?device=<device_id>`，确认认证成功；启动后检查 `capture=armed`、`inference=ready`。不要把 token 粘贴到聊天、命令行明文参数或 GitHub。
4. 中心本机浏览器打开 `http://127.0.0.1:8765/`，使用网页密码登录。网页不要求与香橙派同一局域网。

联网状态取决于底层网络。校园网 Portal 到期时，Tailscale 可能暂时离线；先按 [校园网指南](CAMPUS_NETWORK.md)恢复互联网，再检查 `tailscale status`。常开设备还应评估 Tailscale 的[设备密钥到期设置](https://tailscale.com/docs/how-to/run-unattended)，并在可信设备和现场管理策略下决定是否禁用自动过期。
