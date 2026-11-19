"""Verify the CED-mini release archive and install its four model files."""

import argparse
import hashlib
import json
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--dest", type=Path, default=ROOT / "assets")
    args = parser.parse_args()
    manifest = json.loads((ROOT / "model_manifests" / "ced-mini.json").read_text(encoding="utf-8"))
    archive = args.archive.resolve(strict=True)
    expected_archive = manifest["release_asset"]
    if archive.stat().st_size != expected_archive["bytes"] or digest(archive.read_bytes()) != expected_archive["sha256"]:
        raise SystemExit("CED release archive size or SHA256 mismatch")

    expected = manifest["files"]
    with zipfile.ZipFile(archive) as packed:
        names = packed.namelist()
        if len(names) != len(set(names)) or set(names) != set(expected) | {"orangepi_ced/manifest.json"}:
            raise SystemExit("CED archive has unexpected or duplicate entries")
        verified = {}
        for name, info in expected.items():
            data = packed.read(name)
            if len(data) != info["bytes"] or digest(data) != info["sha256"]:
                raise SystemExit("CED asset checksum mismatch: " + name)
            verified[name] = data

    dest = args.dest.resolve()
    for name, data in verified.items():
        target = dest / name
        if target.exists() and digest(target.read_bytes()) != digest(data):
            raise SystemExit("Existing CED file differs; preserved: " + str(target))
    for name, data in verified.items():
        target = dest / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(data)
    print(f"CED_ASSETS_VERIFIED files={len(verified)} dest={dest}")


if __name__ == "__main__":
    main()
