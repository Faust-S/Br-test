#!/usr/bin/env python3
"""Verify exact vendored Cherry browser files without running upstream code."""
import hashlib
import json
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent

def verify(root, lock):
    base = root / lock["vendor"]
    expected = {}
    for row in lock["files"]:
        path = row["path"]
        p = PurePosixPath(path)
        if not path or path in expected or p.is_absolute() or ".." in p.parts or "\\" in path:
            raise ValueError("invalid_locked_path:" + path)
        expected[path] = row
    if "LICENSE" not in expected:
        raise ValueError("license_missing")
    actual = {p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file()}
    if actual != set(expected):
        raise ValueError("vendor_inventory_mismatch")
    for path, row in expected.items():
        file = base / path
        if file.is_symlink():
            raise ValueError("vendor_symlink:" + path)
        data = file.read_bytes()
        blob = hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()
        if len(data) != row["size"] or blob != row["blob"]:
            raise ValueError("vendor_blob_mismatch:" + path)
    return len(expected)

if __name__ == "__main__":
    count = verify(ROOT, json.loads((ROOT / "source-lock.json").read_text(encoding="utf-8")))
    print("LOCKED_VENDOR_SOURCE_VERIFIED", count)
