#!/usr/bin/env python3
"""Verify exact upstream source intake, without executing upstream code."""
import hashlib
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
LOCK = ROOT / "cherry-browser" / "source-lock.json"

def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.PIPE)

def blob_sha(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()

def verify(repo, lock):
    if git(repo, "rev-parse", "HEAD").decode().strip() != lock["commit"]:
        raise ValueError("upstream_commit_mismatch")
    if git(repo, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("upstream_tracked_worktree_dirty")
    paths = set()
    for row in lock["files"]:
        path = row["path"]
        p = pathlib.PurePosixPath(path)
        if path in paths or p.is_absolute() or ".." in p.parts:
            raise ValueError("invalid_locked_path")
        paths.add(path)
        data = git(repo, "show", "HEAD:" + path)
        if len(data) != row["size"] or blob_sha(data) != row["blob"]:
            raise ValueError("upstream_blob_mismatch:" + path)
        if not (repo / path).is_file():
            raise ValueError("missing_worktree_file:" + path)
    if "LICENSE" not in paths:
        raise ValueError("upstream_license_missing")
    return len(paths)

def main():
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    repo = ROOT / lock["checkout"]
    if not (repo / ".git").exists():
        raise ValueError("upstream_checkout_missing")
    print("LOCKED_SOURCE_VERIFIED", verify(repo, lock))

if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
