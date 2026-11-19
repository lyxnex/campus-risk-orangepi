"""Verify the mirrored deployment source and bundled ESP firmware files."""

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def check(path, info):
    if not path.is_file():
        raise ValueError("Missing file: " + str(path.relative_to(ROOT)))
    data = path.read_bytes()
    if "bytes" in info and len(data) != info["bytes"]:
        raise ValueError("Size mismatch: " + str(path.relative_to(ROOT)))
    if hashlib.sha256(data).hexdigest() != info["sha256"]:
        raise ValueError("SHA256 mismatch: " + str(path.relative_to(ROOT)))


def main():
    source = json.loads((ROOT / "source_manifest.json").read_text(encoding="utf-8"))
    for relative, info in source["files"].items():
        check(ROOT / relative, info)
    firmware = json.loads((ROOT / "firmware" / "esp32s3" / "manifest.json").read_text(encoding="utf-8"))
    for info in firmware["files"]:
        check(ROOT / "firmware" / "esp32s3" / info["path"], info)
    print(f"REPOSITORY_VERIFIED source_files={len(source['files'])} firmware_files={len(firmware['files'])}")


if __name__ == "__main__":
    main()
