"""Assemble an unsigned U01/Obscura release candidate from one qualified CI run.

This stage NEVER publishes a GitHub release, signs a payload, grants installation
authority, or marks an artifact fit for an end user. The existing U01 ArtifactSpec
is the only per-platform installation-data contract. A Publisher and a separate
user decision remain required before an actual product installation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent-headless"))
from iwxa_obscura.preparer import (
    ARCHIVE_LIMIT,
    BINARY_LIMIT,
    ArtifactSpec,
    GUARD_PROTOCOL,
    UPSTREAM_COMMIT,
)

CANDIDATE_SCHEMA = "IWXA_OBSCURA_U01_RELEASE_CANDIDATE/1"
CANDIDATE_INTENT = "AWAITING_PUBLISHER_SIGNATURE_NOT_FOR_USERS"
PLATFORMS = {
    "windows-2022": ("windows-x86_64", "obscura-worker.exe", ".zip"),
    "ubuntu-24.04": ("linux-x86_64", "obscura-worker", ".tar.gz"),
}
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
TOKEN = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}\Z")


def _fail(code: str) -> None:
    raise ValueError(code)


def _sha_stream(stream) -> str:
    digest = hashlib.sha256()
    while block := stream.read(1024 * 1024):
        digest.update(block)
    return digest.hexdigest()


def _sha_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        _fail("CANDIDATE_FILE_INVALID")
    with path.open("rb") as stream:
        return _sha_stream(stream)


def _archive_binary_sha(archive: Path, platform: str, name: str) -> str:
    if not archive.is_file() or archive.is_symlink():
        _fail("CANDIDATE_ARCHIVE_MISSING")
    if not 0 < archive.stat().st_size <= ARCHIVE_LIMIT:
        _fail("CANDIDATE_ARCHIVE_SIZE_INVALID")
    if platform.startswith("windows"):
        try:
            with zipfile.ZipFile(archive, "r") as z:
                rows = z.infolist()
                if len(rows) != 1 or rows[0].filename != name:
                    _fail("CANDIDATE_ARCHIVE_SHAPE_INVALID")
                row = rows[0]
                mode = (row.external_attr >> 16) & 0o170000
                if (row.is_dir() or row.flag_bits & 1
                        or mode == stat.S_IFLNK
                        or not 1024 * 1024 < row.file_size <= BINARY_LIMIT):
                    _fail("CANDIDATE_ARCHIVE_ENTRY_INVALID")
                with z.open(row) as stream:
                    return _sha_stream(stream)
        except (zipfile.BadZipFile, EOFError, OSError, RuntimeError) as exc:
            raise ValueError("CANDIDATE_ARCHIVE_INVALID") from exc
    try:
        with tarfile.open(archive, "r:gz") as z:
            rows = z.getmembers()
            if (len(rows) != 1 or rows[0].name != name
                    or not rows[0].isfile()
                    or not 1024 * 1024 < rows[0].size <= BINARY_LIMIT):
                _fail("CANDIDATE_ARCHIVE_SHAPE_INVALID")
            stream = z.extractfile(rows[0])
            if stream is None:
                _fail("CANDIDATE_ARCHIVE_ENTRY_INVALID")
            with stream:
                return _sha_stream(stream)
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise ValueError("CANDIDATE_ARCHIVE_INVALID") from exc


def candidate_from_native(
    *, artifacts: Path, output: Path, release_id: str, source_sha: str,
    source_run: int, patch: Path, upstream_license: Path,
) -> dict:
    """Require both native jobs, validate all bytes, stage without activation."""
    if (not isinstance(release_id, str) or not TOKEN.fullmatch(release_id)
            or not isinstance(source_sha, str) or not HEX40.fullmatch(source_sha)
            or type(source_run) is not int or source_run <= 0):
        _fail("CANDIDATE_SOURCE_IDENTITY_INVALID")
    if any(word in release_id.lower() for word in ("test", "fixture", "not-for-users")):
        _fail("CANDIDATE_TEST_RELEASE_ID_FORBIDDEN")
    if output.is_symlink() or (output.exists() and (not output.is_dir() or any(output.iterdir()))):
        _fail("CANDIDATE_OUTPUT_NOT_EMPTY")
    if patch.is_symlink() or not patch.is_file() or upstream_license.is_symlink():
        _fail("CANDIDATE_SOURCE_FILE_INVALID")
    raw_patch = patch.read_bytes()
    if b"\r" in raw_patch or not raw_patch:
        _fail("CANDIDATE_PATCH_MUST_BE_LF")
    canonical_patch_sha = hashlib.sha256(raw_patch).hexdigest()
    windows_patch_sha = hashlib.sha256(raw_patch.replace(b"\n", b"\r\n")).hexdigest()
    if not upstream_license.is_file() or not 0 < upstream_license.stat().st_size < 1024 * 1024:
        _fail("CANDIDATE_LICENSE_INVALID")
    staged: list[tuple[Path, str, dict]] = []
    for runner, (platform, executable, ext) in PLATFORMS.items():
        folder = artifacts / ("agent06-headless-" + runner) / "candidate"
        manifest = folder / "candidate-manifest.json"
        if manifest.is_symlink() or not manifest.is_file():
            _fail("CANDIDATE_NATIVE_MANIFEST_MISSING")
        try:
            record = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError) as exc:
            raise ValueError("CANDIDATE_NATIVE_MANIFEST_INVALID") from exc
        fields = {
            "status", "platform", "upstream_commit", "guard_protocol",
            "origin_guard_patch_sha256", "archive_name", "archive_sha256",
            "executable_name", "executable_sha256", "agent_private_source_included",
        }
        if not isinstance(record, dict) or set(record) != fields:
            _fail("CANDIDATE_NATIVE_MANIFEST_FIELDS_INVALID")
        archive_name = "obscura-" + platform + ext
        if (record["status"] != "BUILT_INACTIVE_NOT_RELEASED"
                or record["platform"] != platform
                or record["upstream_commit"] != UPSTREAM_COMMIT
                or record["guard_protocol"] != GUARD_PROTOCOL
                or record["archive_name"] != archive_name
                or record["executable_name"] != executable
                or record["agent_private_source_included"] is not False):
            _fail("CANDIDATE_NATIVE_IDENTITY_INVALID")
        if (not isinstance(record["archive_sha256"], str)
                or not HEX64.fullmatch(record["archive_sha256"])
                or not isinstance(record["executable_sha256"], str)
                or not HEX64.fullmatch(record["executable_sha256"])):
            _fail("CANDIDATE_NATIVE_DIGEST_INVALID")
        allowed_patch = ({canonical_patch_sha, windows_patch_sha}
                         if platform.startswith("windows") else {canonical_patch_sha})
        if record["origin_guard_patch_sha256"] not in allowed_patch:
            _fail("CANDIDATE_PATCH_DIGEST_MISMATCH")
        archive = folder / archive_name
        if _sha_file(archive) != record["archive_sha256"]:
            _fail("CANDIDATE_ARCHIVE_DIGEST_MISMATCH")
        if _archive_binary_sha(archive, platform, executable) != record["executable_sha256"]:
            _fail("CANDIDATE_EXECUTABLE_DIGEST_MISMATCH")
        spec = ArtifactSpec(
            release_id=release_id, platform=platform,
            archive_sha256=record["archive_sha256"],
            executable_sha256=record["executable_sha256"],
            archive_name=archive_name,
            archive_format="zip" if platform.startswith("windows") else "tar.gz",
            download_url=("https://github.com/Faust-S/Br-test/releases/download/"
                          + release_id + "/" + archive_name),
            source_commit=UPSTREAM_COMMIT,
            origin_guard_patch_sha256=record["origin_guard_patch_sha256"],
            guard_protocol=GUARD_PROTOCOL,
        )
        staged.append((archive, archive_name, spec.as_dict()))
    # Only after checking *both* platforms touch output. A partly valid pair
    # is never emitted as an installable release.
    output.mkdir(parents=True, exist_ok=False)
    for src, name, spec in staged:
        destination = output / name
        with src.open("rb") as source, destination.open("xb") as target:
            shutil.copyfileobj(source, target, 1024 * 1024)
        if _sha_file(destination) != spec["archive_sha256"]:
            _fail("CANDIDATE_COPY_DIGEST_MISMATCH")
    license_dest = output / "LICENSE-OBSCURA.txt"
    shutil.copyfile(upstream_license, license_dest)
    result = {
        "schema": CANDIDATE_SCHEMA,
        "intent": CANDIDATE_INTENT,
        "release_id": release_id,
        "source_ci_run": source_run,
        "source_ci_head_sha": source_sha,
        "upstream_commit": UPSTREAM_COMMIT,
        "canonical_patch_sha256": canonical_patch_sha,
        "license_sha256": _sha_file(license_dest),
        "platforms": {spec["platform"]: spec for _, _, spec in staged},
        "signed": False,
        "published": False,
        "approved_for_end_user_install": False,
        "private_agent_artifacts_included": False,
    }
    (output / "manifest.candidate.json").write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--source-head", required=True)
    parser.add_argument("--source-run", type=int, required=True)
    parser.add_argument("--patch", type=Path, required=True)
    parser.add_argument("--license", type=Path, required=True)
    args = parser.parse_args()
    result = candidate_from_native(
        artifacts=args.artifacts,
        output=args.output,
        release_id=args.release_id,
        source_sha=args.source_head,
        source_run=args.source_run,
        patch=args.patch,
        upstream_license=args.license,
    )
    print(json.dumps({
        "status": "U01_UNSIGNED_RELEASE_CANDIDATE",
        "source_ci_run": result["source_ci_run"],
        "platforms": sorted(result["platforms"]),
        "approved_for_end_user_install": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
