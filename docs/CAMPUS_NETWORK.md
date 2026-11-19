# 香橙派 5 Pro 接入 BUPT-portal 后使用 Tailscale

本页根据 2026-11-19 在 Orange Pi 5 Pro、Ubuntu 22.04 Jammy Server、`orangepi` 用户和 NetworkManager 上的实际流程整理。其他系统的网卡名、用户名及包管理命令需按现场调整。先在板子本地接显示器和键盘：切换 Wi-Fi 或 Portal 重新认证时，原有 SSH 会断开。跨网项目通信继续看 [TAILSCALE_CROSS_NETWORK.md](TAILSCALE_CROSS_NETWORK.md)。

## 1. 用临时网络完成首次安装

将指定的 Orange Pi 5 Pro 系统镜像写入 microSD，启动并登录。先接可用的临时热点或有线网络，在板端运行：

```bash
nmcli device status
sudo nmcli device wifi rescan ifname wlan0
nmcli device wifi list ifname wlan0
sudo nmcli --ask device wifi connect "<临时热点SSID>" ifname wlan0
ip -4 -br addr show wlan0
sudo apt-get update
sudo apt-get install -y openssh-server curl
sudo systemctl enable --now ssh
systemctl is-active ssh
```

将 `wlan0` 换成 `nmcli device status` 显示的实际 Wi-Fi 接口。热点密码由 `--ask` 在板端交互输入，不写入仓库。接下来按 [Tailscale 跨网指南](TAILSCALE_CROSS_NETWORK.md)在板端和 Windows 中心各自安装、授权 Tailscale；暂时在热点上测通 `ssh orangepi@<板端TailscaleIPv4>`。

## 2. 准备校园网 Portal 认证

[北邮校园网说明](https://welcome.bupt.edu.cn/info/1003/1084.htm)列出 `BUPT-portal` 的网页 Portal 认证方式；初始密码需先在学校账户页面修改，正式学号和密码只在本人设备输入。本项目没有将校园网账号放入 GitHub。

没有图形浏览器的板端可使用第三方 [bupt-net-login](https://github.com/charlie0129/bupt-net-login)。它不是本仓库源码，使用前应查看项目说明和脚本内容。趁临时网络有网，将脚本下载到临时文件并做 Bash 语法检查：

```bash
curl -4fL --connect-timeout 8 --max-time 60 \
  https://raw.githubusercontent.com/charlie0129/bupt-net-login/master/bupt-net-login \
  -o /tmp/bupt-net-login.new
test -s /tmp/bupt-net-login.new && bash -n /tmp/bupt-net-login.new && \
  install -m 700 /tmp/bupt-net-login.new ~/bupt-net-login
```

若板端下载失败，可以在已登录的 Windows 电脑从项目页面保存 **Raw 脚本**，再通过 `scp` 传到 `/home/orangepi/bupt-net-login`，在板端运行 `test -s ~/bupt-net-login && bash -n ~/bupt-net-login && chmod 700 ~/bupt-net-login`。不要把 GitHub HTML 页面误存为脚本，也不要让失败的下载覆盖已有可用脚本。

## 3. 本地切换到校园 Wi-Fi 并认证

在**香橙派本地终端**执行：

```bash
sudo nmcli device wifi rescan ifname wlan0
nmcli device wifi list ifname wlan0
sudo nmcli device wifi connect "BUPT-portal" ifname wlan0
ip -4 -br addr show wlan0
bash ~/bupt-net-login
curl -4sS --max-time 10 http://captive.apple.com/
tailscale status
```

脚本会交互询问学号和已修改的校园网密码。`curl` 预期返回 `Success`；出现 Portal 页面或连接错误表示仍未上网。只有 Wi-Fi 获得 IPv4 地址，并不能证明已经完成 Portal 认证。重新联网后检查 Tailscale 是否在线，再从 Windows 经板端 Tailscale 地址测试 SSH。

如果曾保存临时热点连接，可把校园网设为更高的自动连接优先级：

```bash
sudo nmcli connection modify BUPT-portal connection.autoconnect yes connection.autoconnect-priority 100
nmcli connection show
```

其他已保存连接的优先级可按现场调整；该设置不会立即切断当前连接。不要把某人的热点 SSID 写死进项目脚本。

## 4. 可选：Portal 过期后自动重认证

常开板若需要无人值守重认证，需把校园网凭据保存为该板 `orangepi` 用户可读的本地文件。**这会以明文形式保存在板上**；不接受这一做法的现场应改用手动认证或学校提供的其他受支持方式。先在板端交互输入，不放入 shell 命令历史：

```bash
read -r -p '学号: ' CAMPUS_ID
read -rs -p '校园网上网密码: ' CAMPUS_PW; echo
printf -v QUOTED_ID '%q' "$CAMPUS_ID"
printf -v QUOTED_PW '%q' "$CAMPUS_PW"
(umask 077; printf 'BUPT_USERNAME=%s\nBUPT_PASSWORD=%s\n' "$QUOTED_ID" "$QUOTED_PW" > /home/orangepi/.bupt-net-login)
chmod 600 /home/orangepi/.bupt-net-login
unset CAMPUS_ID CAMPUS_PW QUOTED_ID QUOTED_PW
```

创建只运行第三方脚本的服务和每两分钟复查一次的定时器：

```bash
sudo tee /etc/systemd/system/bupt-portal-auth.service >/dev/null <<'EOF'
[Unit]
Description=Check BUPT portal authentication
After=NetworkManager.service

[Service]
Type=oneshot
User=orangepi
Environment=HOME=/home/orangepi
ExecStart=/usr/bin/bash /home/orangepi/bupt-net-login
TimeoutStartSec=60
EOF
sudo tee /etc/systemd/system/bupt-portal-auth.timer >/dev/null <<'EOF'
[Unit]
Description=Check BUPT portal authentication every two minutes

[Timer]
OnBootSec=1min
OnUnitInactiveSec=2min
AccuracySec=10s
Unit=bupt-portal-auth.service

[Install]
WantedBy=timers.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now bupt-portal-auth.timer
systemctl list-timers --all bupt-portal-auth.timer
```

需要检查时，在板端运行 `sudo systemctl start bupt-portal-auth.service` 和 `journalctl -u bupt-portal-auth.service -n 30 --no-pager`；随后再次检查 `curl -4sS --max-time 10 http://captive.apple.com/`、`tailscale status`。定时器启用不等于过期后的重新认证已通过，需在真实过期和重启后分别验证。

## 故障定位顺序

1. `nmcli device status`：Wi-Fi 是否连到预期网络。
2. `curl -4sS --max-time 10 http://captive.apple.com/`：Portal 是否已认证。
3. `systemctl is-active tailscaled`、`tailscale status`：板端是否在线并处于正确 tailnet。
4. Windows 执行 `Test-NetConnection <板端TailscaleIPv4> -Port 22`，板端执行 `curl -fsS http://<中心TailscaleIPv4>:8765/healthz`。

Wi-Fi、Portal、Tailscale、SSH、项目中心是连续的不同层；按上述顺序定位，不要用复制旧设备 Tailscale 状态来修复连接。
