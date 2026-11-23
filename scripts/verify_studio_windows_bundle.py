#!/usr/bin/env python3
"""Validate and describe the exact Windows Studio payload produced by CI."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def describe(path: Path, root: Path) -> dict[str, object]:
    return {
        "name": path.name,
        "path": path.relative_to(root).as_posix(),
        "size_bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--core", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()

    bundle_root = args.bundle_root.resolve()
    core = args.core.resolve()
    config_path = args.config.resolve()

    missing = [str(path) for path in (bundle_root, core, config_path) if not path.exists()]
    if missing:
        raise SystemExit("Required build output is missing: " + ", ".join(missing))

    config = json.loads(config_path.read_text(encoding="utf-8"))
    installers = sorted(
        [*bundle_root.glob("msi/*.msi"), *bundle_root.glob("nsis/*.exe")],
        key=lambda item: item.as_posix().lower(),
    )
    if not any(path.suffix.lower() == ".msi" for path in installers):
        raise SystemExit("No MSI installer was generated.")
    if not any(path.suffix.lower() == ".exe" for path in installers):
        raise SystemExit("No NSIS EXE installer was generated.")
    if not installers or core.stat().st_size == 0:
        raise SystemExit("Core or installer output is empty.")

    manifest = {
        "schema_version": 1,
        "product": config.get("productName", "KardecAgent Studio"),
        "version": config.get("version", "unknown"),
        "git_commit": os.environ.get("GITHUB_SHA", "local"),
        "git_ref": os.environ.get("GITHUB_REF", "local"),
        "platform": "windows-x64",
        "core": describe(core, core.parent),
        "installers": [describe(path, bundle_root) for path in installers],
    }
    output = bundle_root / "studio-build-manifest.json"
    output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"Build manifest: {output}")
    print(f"Product: {manifest['product']} {manifest['version']}")
    print(f"Core: {core.stat().st_size:,} bytes | SHA-256 {manifest['core']['sha256']}")
    for installer in manifest["installers"]:
        print(
            f"Installer: {installer['path']} | {installer['size_bytes']:,} bytes "
            f"| SHA-256 {installer['sha256']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
