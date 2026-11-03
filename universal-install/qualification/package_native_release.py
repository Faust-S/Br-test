"""Package six *already qualified* native IWXA Obscura Workers for an isolated test prerelease.

No Rust build and no product release. All bytes come from public GitHub Actions
artifacts of one exact successful commit, and are independently re-hashed.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent-headless"))
from iwxa_obscura.preparer import ArtifactSpec, GUARD_PROTOCOL, UPSTREAM_COMMIT

TARGETS = {
    "windows-2022": ("windows-x86_64", "Windows", "obscura-worker.exe"),
    "windows-11-arm": ("windows-arm64", "Windows", "obscura-worker.exe"),
    "ubuntu-24.04": ("linux-x86_64", "Linux", "obscura-worker"),
    "ubuntu-24.04-arm": ("linux-arm64", "Linux", "obscura-worker"),
    "macos-15-intel": ("macos-x86_64", "macOS", "obscura-worker"),
    "macos-15": ("macos-arm64", "macOS", "obscura-worker"),
}
TEST_RELEASE_SCHEMA = "IWXA_NATIVE_TEST_RELEASE/1.0"


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def validate_report(info: dict, platform: str, expected_sha: str, patch_sha: str) -> None:
    required = {
        "schema": "IWXA_UNIVERSAL_NATIVE_INSTALL_CI/1.0",
        "platform": platform,
        "source_commit": UPSTREAM_COMMIT,
        "patch_sha256": patch_sha,
        "status": "PASS",
        "real_native_binary": True,
        "published_release": False,
        "tool_runtime_connected": False,
        "new_version_removed_after_corruption": True,
        "old_version_survived_new_version_removal": True,
        "old_version_removed_after_worker_shutdown": True,
        "native_attestation": "IWXA_ORIGIN_NET_GUARD_V1_NO_RENDER",
        "fresh_process_greeting": GUARD_PROTOCOL,
        "build_binary_sha256": expected_sha,
        "installed_binary_sha256": expected_sha,
    }
    for key, value in required.items():
        if info.get(key) != value:
            raise ValueError(f"CI_EVIDENCE_INVALID:{platform}:{key}")


def create_archive(source: Path, output: Path, member: str) -> None:
    """One executable, no extra files, no absolute paths, controlled metadata."""
    if output.suffix == ".zip":
        info = zipfile.ZipInfo(member, (2020, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100755 << 16
        with zipfile.ZipFile(output, mode="x", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=9) as archive:
            with archive.open(info, "w", force_zip64=True) as target:
                with source.open("rb") as stream:
                    while chunk := stream.read(1024 * 1024):
                        target.write(chunk)
    else:
        with output.open("xb") as archive_file:
            with gzip.GzipFile(fileobj=archive_file, mode="wb", filename="", mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode="w") as tar:
                    info = tarfile.TarInfo(member)
                    info.size = source.stat().st_size
                    info.mode = 0o755
                    info.uid = info.gid = 0
                    info.mtime = 0
                    with source.open("rb") as stream:
                        tar.addfile(info, stream)


def package(artifacts: Path, dest: Path, tag: str, source_run: int,
            source_sha: str, patch_sha: str, windows_patch_sha: str | None = None) -> dict:
    if dest.exists() and any(dest.iterdir()):
        raise ValueError("OUTPUT_NOT_EMPTY")
    dest.mkdir(parents=True, exist_ok=True)
    info = {
        "schema": TEST_RELEASE_SCHEMA,
        "intent": "CI_ONLY_NO_END_USER_RELEASE",
        "release_id": tag,
        "native_source_run_id": source_run,
        "native_source_head_sha": source_sha,
        "upstream_commit": UPSTREAM_COMMIT,
        "origin_guard_patch_sha256": patch_sha,
        "platforms": {},
    }
    for runner, (platform, operating_system, name) in TARGETS.items():
        folder = artifacts / f"universal-native-obscura-{runner}"
        if not folder.is_dir():
            raise ValueError(f"NATIVE_ARTIFACT_MISSING:{runner}")
        reports = list(folder.rglob(f"native-{operating_system}.json"))
        if len(reports) != 1:
            raise ValueError(f"NATIVE_REPORT_MISSING_OR_DUPLICATE:{runner}")
        report = json.loads(reports[0].read_text(encoding="utf-8"))
        binary_sha = report.get("build_binary_sha256")
        if not isinstance(binary_sha, str) or len(binary_sha) != 64:
            raise ValueError(f"NATIVE_DIGEST_MISSING:{runner}")
        # On Windows checkout, Git may convert the same text patch to CRLF.
        # Accept ONLY that deterministic transformation of the pinned LF
        # original; never accept a patch hash supplied by untrusted evidence.
        platform_patch_sha = (windows_patch_sha if platform.startswith("windows")
                              and windows_patch_sha is not None else patch_sha)
        validate_report(report, platform, binary_sha, platform_patch_sha)
        binaries = [p for p in folder.rglob(name) if p.is_file() and digest(p) == binary_sha]
        if len(binaries) != 1 or binaries[0].stat().st_size <= 1024 * 1024:
            raise ValueError(f"NATIVE_BINARY_MISSING_OR_INVALID:{runner}")
        extension = ".zip" if platform.startswith("windows") else ".tar.gz"
        archive_name = "obscura-" + platform + extension
        archive_path = dest / archive_name
        create_archive(binaries[0], archive_path, name)
        spec = ArtifactSpec(
            release_id=tag, platform=platform,
            archive_sha256=digest(archive_path), executable_sha256=binary_sha,
            archive_name=archive_name,
            archive_format="zip" if extension == ".zip" else "tar.gz",
            download_url=("https://github.com/Faust-S/Br-test/releases/download/"
                          + tag + "/" + archive_name),
            source_commit=UPSTREAM_COMMIT,
            origin_guard_patch_sha256=platform_patch_sha,
            guard_protocol=GUARD_PROTOCOL,
        )
        info["platforms"][platform] = spec.as_dict()
    (dest / "manifest.test-only.json").write_text(
        json.dumps(info, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return info


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-run", type=int, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--patch", type=Path, required=True)
    parser.add_argument("--windows-patch-sha", required=True, help="Pinned Windows patch digest from the qualified native run")
    args = parser.parse_args()
    patch_bytes = args.patch.read_bytes()
    if b"\\r\\n" in patch_bytes:
        raise ValueError("PACKAGING_PATCH_MUST_USE_CANONICAL_LF")
    sha = hashlib.sha256(patch_bytes).hexdigest()
    windows_patch_sha = args.windows_patch_sha
    if len(windows_patch_sha) != 64 or any(x not in "0123456789abcdef" for x in windows_patch_sha):
        raise ValueError("WINDOWS_PATCH_DIGEST_INVALID")
    info = package(args.artifacts, args.output, args.tag, args.source_run,
                   args.source_sha, sha, windows_patch_sha)
    print(json.dumps({
        "status": "PACKAGED_TEST_CANDIDATE",
        "release_id": info["release_id"],
        "source_run": info["native_source_run_id"],
        "platforms": {k: v["executable_sha256"] for k, v in info["platforms"].items()},
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
