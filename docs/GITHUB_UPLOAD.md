# GitHub 上传范围

只把本目录作为仓库根。不要在整个历史 Codex 工作目录执行 `git add .`。

目标已确认为用户个人私人仓库 `lyxnex/campus-risk-orangepi`。旧板只读导出确认源码、Python 依赖和资产清单；推送前仍须检查暂存文件和远端状态。

## 普通代码提交

- edge、center 源码，模板配置，systemd 模板，导出工具和文档。
- `source_manifest.json`、`docs/board-snapshot.json` 追踪已核验源码和平台；`requirements/edge.lock.txt`、`assets.lock.json` 记录旧板实际依赖与资产清单。
- 推送前检查 `git status` 和待提交文件；不提交现场配置、录音、训练样本或凭据。

## Release 附件或 LFS

当前 Q8 约 1.9 GB，普通 GitHub Git 提交会超过 100 MiB 文件限制。建议发布独立私有 Release 附件：

1. 当前 Q8 GGUF（已有固定 SHA256）。
2. SenseVoice 模型、tokens 与 manifest。
3. RBT3 模型、tokenizer 与融合 manifest。
4. 已验证的 Linux ARM64 llama-server 与依赖说明。
5. 从旧板导出的依赖锁定清单；后续若制作离线 ARM64 wheels，也单独作为附件。

当前已核验的本地 Q8 可单独上传；旧板可用 `tools/package_board_assets.py` 按 `assets.lock.json` 生成一个只含 SenseVoice、RBT3 和 ARM runtime 的 `nonqwen-assets.tar`。新板解包时恢复归档内的项目相对路径，逐文件对照 `assets.lock.json`。该工具不读取或打包录音、待传包及正式配置。

每个附件记录 SHA256、大小、目标路径、版本、来源及必要许可。不要把旧 `.venv` 当作可跨系统迁移的安装包。Release 下载成功后仍需校验，不应把 Token 放入 URL、README 或提交历史。

官方大文件规则：https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github

## 发布前缺口

仍需准备附件及可重建环境，完成新板安装和端到端验收。尚未完成这些步骤前，不能称 `git clone` 已经实现一键部署。
