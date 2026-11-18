"""Package only the non-Qwen assets recorded by a read-only board export.

This does not stop services, change source assets, or include deployment data.
"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tarfile


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=Path("/home/orangepi/campus-risk"))
    parser.add_argument("--export-dir", type=Path, required=True)
    args = parser.parse_args()
    base = args.base.resolve(strict=True)
    export_dir = args.export_dir.resolve(strict=True)
    if not export_dir.is_relative_to(base / "migration_exports"):
        raise ValueError("Export directory must be inside migration_exports")
    lock = json.loads((export_dir / "source" / "assets.lock.json").read_text("utf-8"))
    rows = [row for row in lock["files"] if row["group"] != "qwen"]
    if not rows or {row["group"] for row in rows} != {"sensevoice", "rbt3", "runtime"}:
        raise ValueError("Expected SenseVoice, RBT3 and runtime asset groups")
    files = []
    for row in rows:
        relative = Path(row["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unsafe asset path")
        path = base / relative
        if path.is_symlink() or not path.resolve(strict=True).is_relative_to(base):
            raise ValueError("Symlink or path outside project")
        if not path.is_file() or path.stat().st_size != row["bytes"] or digest(path) != row["sha256"]:
            raise ValueError("Asset differs from board snapshot: " + row["path"])
        files.append((path, row["path"]))
    required = sum(row["bytes"] for row in rows) + 128 * 1024 * 1024
    if shutil.disk_usage(export_dir).free < required:
        raise RuntimeError("Insufficient free space; no archive created")
    archive = export_dir / "nonqwen-assets.tar"
    with tarfile.open(archive, "x") as tar:
        for path, relative in files:
            tar.add(path, arcname=relative, recursive=False)
    result = {
        "archive": str(archive),
        "bytes": archive.stat().st_size,
        "sha256": digest(archive),
        "asset_files": len(rows),
        "qwen_included": False,
        "recordings_included": False,
        "service_restarted": False,
    }
    print("NONQWEN_ASSETS_COMPLETE " + json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
