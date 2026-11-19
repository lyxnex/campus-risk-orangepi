# Python 环境

当前运行环境在旧板 `/home/orangepi/campus-risk/.venv`，不是 Conda。2026-11-18 的只读导出确认 Python 3.10.12、Linux aarch64；实际已安装包版本见 `edge.lock.txt`。主要依赖为 numpy、sherpa-onnx、torch、transformers、safetensors、pyserial 等。

运行 `tools/export_running_board.py` 会使用旧板 `.venv/bin/python -m pip list --format=json` 生成仅含包名/版本的清单，避免把 pip direct URL 中的凭据带入仓库。本目录 `edge.lock.txt` 是该清单的已核验副本。

该清单是整个已安装环境的快照，不代表最小依赖。2026-11-19 新板实测：普通 PyPI 无法解析 `torch==2.9.1+cpu`，增加 `--extra-index-url https://download.pytorch.org/whl/cpu` 后在 Python 3.10/aarch64 上安装成功。新板应使用同版本操作系统和匹配的 Python minor，重新创建 venv，并做模块导入及实际推理验证。ARM runtime 的系统动态库见 `../docs/board-snapshot.json`。

复制 `.venv`、Windows Python 或 x86 wheels 不能替代 ARM64 环境准备。
