"""Create private center and per-board pairing configs for a new installation."""

import argparse
import getpass
import json
import os
from pathlib import Path
import re
import secrets
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "center"))
from web_auth import password_hash  # noqa: E402


def write_private(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--center-host", required=True, help="Address reachable from Orange Pi boards")
    parser.add_argument("--device-id", action="append", required=True, help="Repeat for every board")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "private")
    args = parser.parse_args()

    host = args.center_host.strip()
    if not re.fullmatch(r"[A-Za-z0-9.-]+", host) or host in {"localhost", "127.0.0.1", "0.0.0.0"}:
        parser.error("--center-host must be a LAN or Tailscale address reachable from the boards")
    if not 1 <= args.port <= 65535:
        parser.error("--port is outside 1..65535")
    devices = args.device_id
    if len(devices) != len(set(devices)) or not all(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", d) for d in devices):
        parser.error("device IDs must be unique and contain only letters, digits, '_' or '-'")

    out = args.output_dir.resolve()
    center_path = out / "center_config.json"
    board_paths = [out / "pairing" / f"{d}.json" for d in devices]
    existing = [str(p) for p in [center_path, *board_paths] if p.exists()]
    if existing:
        parser.error("existing private config preserved: " + ", ".join(existing))

    first = getpass.getpass("New center webpage password (8–128 characters): ")
    if first != getpass.getpass("Repeat webpage password: "):
        parser.error("passwords do not match")
    try:
        encoded = password_hash(first)
    except ValueError as exc:
        parser.error(str(exc))

    token = secrets.token_urlsafe(48)
    center = json.loads((ROOT / "config" / "center.example.json").read_text(encoding="utf-8"))
    center.update(
        port=args.port,
        token=token,
        web_password_hash=encoded,
        allowed_devices=devices,
        data_dir="center_data",
        ced_model_dir=os.path.relpath(ROOT / "assets" / "orangepi_ced" / "ced-mini", out),
    )
    write_private(center_path, center)

    for device, path in zip(devices, board_paths):
        edge = json.loads((ROOT / "config" / "edge.example.json").read_text(encoding="utf-8"))
        suffix = device.rsplit("-", 1)[-1]
        scene = suffix if suffix.isdigit() else device
        edge.update(
            device_id=device,
            scene_id=f"scene-{scene}",
            scene_name=f"采集点{int(scene) if scene.isdigit() else scene}",
            center_url=f"http://{host}:{args.port}",
            token=token,
            state_dir=f"/home/orangepi/campus-risk/deployment_data_site_{device}",
        )
        write_private(path, edge)

    print(f"SITE_CONFIG_CREATED center={center_path} boards={len(board_paths)}")
    print("Edit each pairing file's port after reading that board's /dev/serial/by-id/ path; do not commit private/.")


if __name__ == "__main__":
    main()
