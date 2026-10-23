"""Verify a stopped-instance Neo4j backup set without treating it as a restore.

This is an integrity inventory only, not an online consistent snapshot mechanism.
The operator must stop writers and take a database-consistent backup separately.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

MANIFEST_NAME = "fractal-backup-manifest.json"
MAX_FILE_BYTES = 50 * 1024 * 1024 * 1024


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def inventory(directory: Path) -> dict:
    root = directory.expanduser().resolve()
    if not root.is_dir():
        raise ValueError("backup directory not found")
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"backup must not contain symlinks: {path}")
        if not path.is_file() or path.name == MANIFEST_NAME:
            continue
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError(f"backup file exceeds limit: {path}")
        files.append({"path": path.relative_to(root).as_posix(),
                      "bytes": path.stat().st_size, "sha256": _digest(path)})
    if not files:
        raise ValueError("backup directory contains no backup files")
    return {"format": "fractal-backup-inventory-v1",
            "database_consistency": "NOT_VERIFIED",
            "restore": "NOT_VERIFIED",
            "files": files}


def write_manifest(directory: Path) -> Path:
    root = directory.expanduser().resolve()
    manifest = inventory(root)
    path = root / MANIFEST_NAME
    if path.exists():
        raise FileExistsError(path)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def verify_manifest(directory: Path) -> dict:
    root = directory.expanduser().resolve()
    path = root / MANIFEST_NAME
    saved = json.loads(path.read_text(encoding="utf-8"))
    if saved.get("format") != "fractal-backup-inventory-v1":
        raise ValueError("unsupported backup manifest format")
    expected = saved.get("files")
    if not isinstance(expected, list) or not expected:
        raise ValueError("missing backup manifest entries")
    actual = inventory(root)["files"]
    if actual != expected:
        raise ValueError("backup inventory mismatch (modified, missing or extra files)")
    return {"status": "HASHES_VERIFIED", "files": len(actual),
            "database_consistency": "NOT_VERIFIED", "restore": "NOT_VERIFIED"}
