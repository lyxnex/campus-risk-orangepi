# GitHub 上传范围

只把本目录作为仓库根。不要在整个历史 Codex 工作目录执行 `git add .`。

源码和资产清单已推送到用户个人私人仓库 `lyxnex/campus-risk-orangepi`。旧板只读导出确认源码、Python 依赖和资产清单；预发行版标签 `v0.1.0-migration` 指向已核验的仓库提交。
后续中心与固件交接补充在 `main` 分支；该 Release 的自动源码 ZIP 来自较早标签。新安装应克隆 `main`，只从 Release 下载三个模型附件。

## 普通代码提交

- edge、center 源码，模板配置，systemd 模板，导出工具和文档。
- `source_manifest.json`、`docs/board-snapshot.json` 追踪已核验源码和平台；`requirements/edge.lock.txt`、`assets.lock.json` 记录旧板实际依赖与资产清单。
- 推送前检查 `git status` 和待提交文件；不提交现场配置、录音、训练样本或凭据。

## Release 附件或 LFS

当前 Q8 约 1.9 GB，普通 GitHub Git 提交会超过 100 MiB 文件限制。已发布[私人预发行版 `v0.1.0-migration`](https://github.com/lyxnex/campus-risk-orangepi/releases/tag/v0.1.0-migration)，含三个独立附件：

1. 当前 Q8 GGUF，2,012,012,128 字节。
2. `nonqwen-assets.tar`，409,456,640 字节，含 SenseVoice、RBT3 和 Linux ARM64 llama-server 共 12 项。
3. `ced-mini-assets.zip`，7,219,101 字节，含 Windows 中心 CED-mini 模型与预处理文件。

CED-mini 是额外的中心辅助模型；其代码在 `center/capture/ced_observer.py`，校验信息在 `model_manifests/ced-mini.json`。它不参与板端 RBT3→Qwen 判决。

GitHub 返回的三个附件 SHA256 均与 `release_assets.json` 一致；板端归档内部文件与 `assets.lock.json` 一致，CED 归档内部模型与 `model_manifests/ced-mini.json` 一致。依赖清单在 `requirements/`，不作为附件。后续若制作离线 ARM64 wheels，应单独发布和校验。

每个附件记录 SHA256、大小、目标路径、版本、来源及必要许可。不要把旧 `.venv` 当作可跨系统迁移的安装包。Release 下载成功后仍需校验，不应把 Token 放入 URL、README 或提交历史。

官方大文件规则：https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github

## 当前验收边界

第二块同镜像新板已经从仓库源码与两个板端附件完成安装，现场讲话采集、推理、中心设备认证和 systemd 自启设置通过。风险候选 ACK、网页音频、断网重传和重启恢复尚待真实事件/现场维护窗口验证。Windows 中心的源码安装流程已补全，但全新电脑上的开机自动启动与防火墙步骤尚需实机验收；不能称为已验证的一键离线安装器。
