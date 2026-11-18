"""Verify every installed model and runtime file against assets.lock.json."""

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    args = parser.parse_args()
    base = args.base.resolve(strict=True)
    rows = json.loads(args.lock.read_text("utf-8"))["files"]
    if len(rows) != 13 or {row["group"] for row in rows} != {"sensevoice", "rbt3", "qwen", "runtime"}:
        raise ValueError("Unexpected asset manifest")
    seen = set()
    for row in rows:
        relative = Path(row["path"])
        if relative.is_absolute() or ".." in relative.parts or row["path"] in seen:
            raise ValueError("Unsafe or duplicate asset path")
        seen.add(row["path"])
        path = base / relative
        if path.is_symlink() or not path.resolve(strict=True).is_relative_to(base):
            raise ValueError("Symlink or asset outside base")
        if path.stat().st_size != row["bytes"] or sha256(path) != row["sha256"]:
            raise ValueError("Asset differs: " + row["path"])
    print("ASSETS_VERIFIED files=13", flush=True)


if __name__ == "__main__":
    main()
