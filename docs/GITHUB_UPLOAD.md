# GitHub 上传范围

只把本目录作为仓库根。不要在整个历史 Codex 工作目录执行 `git add .`。

源码和资产清单已推送到用户个人私人仓库 `lyxnex/campus-risk-orangepi`。旧板只读导出确认源码、Python 依赖和资产清单；预发行版标签 `v0.1.0-migration` 指向已核验的仓库提交。

## 普通代码提交

- edge、center 源码，模板配置，systemd 模板，导出工具和文档。
- `source_manifest.json`、`docs/board-snapshot.json` 追踪已核验源码和平台；`requirements/edge.lock.txt`、`assets.lock.json` 记录旧板实际依赖与资产清单。
- 推送前检查 `git status` 和待提交文件；不提交现场配置、录音、训练样本或凭据。

## Release 附件或 LFS

当前 Q8 约 1.9 GB，普通 GitHub Git 提交会超过 100 MiB 文件限制。已发布[私人预发行版 `v0.1.0-migration`](https://github.com/lyxnex/campus-risk-orangepi/releases/tag/v0.1.0-migration)，含两个独立附件：

1. 当前 Q8 GGUF，2,012,012,128 字节。
2. `nonqwen-assets.tar`，409,456,640 字节，含 SenseVoice、RBT3 和 Linux ARM64 llama-server 共 12 项。

GitHub 返回的两个附件 SHA256 均与 `release_assets.json` 一致；归档内部每个文件也与 `assets.lock.json` 一致。依赖锁定清单在 `requirements/edge.lock.txt`，不作为附件。后续若制作离线 ARM64 wheels，应单独发布和校验。

每个附件记录 SHA256、大小、目标路径、版本、来源及必要许可。不要把旧 `.venv` 当作可跨系统迁移的安装包。Release 下载成功后仍需校验，不应把 Token 放入 URL、README 或提交历史。

官方大文件规则：https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github

## 发布前缺口

仍需在新板重建匹配的 Python 环境、导入本板私密配置，并完成实机安装和端到端验收。尚未完成这些步骤前，不能称 `git clone` 已经实现一键部署。
