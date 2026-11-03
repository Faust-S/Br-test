"""End-to-end installation check of a *real compiled* IWXA-patched Obscura Worker.

Runs only in ephemeral test profiles. Does not install into a real user's OS,
does not connect a provider, and never claims this artifact is a released build.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import io
import json
import os
import platform as host_platform
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE / "universal-install"))
sys.path.insert(0, str(HERE / "agent-headless"))
from iwxa_obscura.handshake import GUARD_GREETING, require_worker_greeting
from iwxa_obscura.preparer import ArtifactSpec, GUARD_PROTOCOL, UPSTREAM_COMMIT
from iwxa_universal_install import ApprovedInstall, ApprovedRemoval, InstallError, ObscuraManagedBackend


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--patch", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    start = time.monotonic()
    source = args.binary.resolve(strict=True)
    patch = args.patch.resolve(strict=True)
    arch = host_platform.machine().lower()
    if arch in ("amd64", "x86_64"):
        arch = "x86_64"
    elif arch in ("aarch64", "arm64"):
        arch = "arm64"
    else:
        raise RuntimeError("UNSUPPORTED_NATIVE_ARCH")
    if os.name == "nt":
        platform = "windows-" + arch
    elif sys.platform == "linux":
        platform = "linux-" + arch
    elif sys.platform == "darwin":
        platform = "macos-" + arch
    else:
        raise RuntimeError("UNSUPPORTED_NATIVE_OS")
    if platform not in ("windows-x86_64", "windows-arm64",
                        "linux-x86_64", "linux-arm64",
                        "macos-x86_64", "macos-arm64"):
        raise RuntimeError("UNQUALIFIED_NATIVE_PLATFORM")
    exe_name = "obscura-worker.exe" if os.name == "nt" else "obscura-worker"
    assert source.name == exe_name, "actual native compiled worker binary required"
    assert source.stat().st_size > 1024 * 1024, "reject synthetic fixture"
    native_sha = hash_file(source)
    patch_sha = hash_file(patch)
    assert len(patch_sha) == 64

    with tempfile.TemporaryDirectory(prefix="iwxa-native-install-") as td:
        tmp = Path(td).resolve()
        install_root = tmp / "isolated-installed-product"
        staging_root = tmp / "isolated-stage"
        archive_name = "iwxa-obscura-" + platform + (".zip" if os.name == "nt" else ".tar.gz")
        archive = tmp / archive_name
        if os.name == "nt":
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as file:
                file.write(source, arcname=exe_name)
        else:
            with tarfile.open(archive, "w:gz") as file:
                file.add(source, arcname=exe_name, recursive=False)
        archive_sha = hash_file(archive)
        release = "ci-native-" + native_sha[:16]
        # This is a local ephemeral CI package, NOT a published release.
        spec = ArtifactSpec(
            release_id=release, platform=platform,
            archive_sha256=archive_sha, executable_sha256=native_sha,
            download_url=(
                "https://github.com/Faust-S/Br-test/releases/download/"
                + release + "/" + archive_name
            ),
            archive_name=archive_name,
            archive_format="zip" if os.name == "nt" else "tar.gz",
            source_commit=UPSTREAM_COMMIT,
            origin_guard_patch_sha256=patch_sha,
            guard_protocol=GUARD_PROTOCOL,
        )
        backend = ObscuraManagedBackend()
        assert backend.inspect(spec, str(install_root)).status == "NOT_INSTALLED"
        try:
            backend.apply(spec, str(staging_root), ApprovedInstall(
                "OBSCURA_WORKER_V1", spec.fingerprint, "PINS-no-consent",
                str(install_root), False,
            ))
            raise AssertionError("install without host confirmation was permitted")
        except InstallError as error:
            assert error.code == "INSTALL_OWNER_APPROVAL_REQUIRED", error.code
        assert not install_root.exists()
        prepared = backend.prepare(spec, str(staging_root), archive_path=str(archive))
        assert prepared.status == "STAGED_NOT_ACTIVATED"
        assert backend.verify(spec, str(install_root)).status == "NOT_INSTALLED"
        approval = ApprovedInstall(
            program_id=backend.program_id,
            spec_fingerprint=spec.fingerprint,
            receipt_id="PINS-ci-native-" + native_sha[:16],
            installation_root=str(install_root), confirmed=True,
        )
        installed = backend.apply(spec, str(staging_root), approval)
        assert installed.status == "INSTALLED_NOT_CONNECTED", installed
        assert installed.installed and not installed.connected and not installed.receipt_written
        target = Path(installed.executable)
        assert target.is_file()
        assert target != source and target != Path(prepared.executable)
        assert hash_file(target) == native_sha
        assert backend.recover(spec, str(install_root)) == installed
        assert ObscuraManagedBackend().verify(spec, str(install_root)) == installed
        assert backend.apply(spec, str(staging_root), approval) == installed

        # An update is a separate pinned release and must not overwrite older
        # versions or perform provider activation.
        next_release = "ci-update-" + native_sha[:16]
        updated = replace(spec, release_id=next_release,
                          download_url=spec.download_url.replace(
                              spec.release_id, next_release))
        backend.prepare(updated, str(staging_root), archive_path=str(archive))
        updated_approval = ApprovedInstall(
            program_id=backend.program_id, spec_fingerprint=updated.fingerprint,
            receipt_id="PINS-ci-update-" + native_sha[:16],
            installation_root=str(install_root), confirmed=True,
        )
        newer = backend.apply(updated, str(staging_root), updated_approval)
        assert newer.installed and newer.executable != installed.executable
        assert backend.recover(spec, str(install_root)) == installed
        assert backend.recover(updated, str(install_root)) == newer

        profile = tmp / "isolated-runtime-profile"
        profile.mkdir()
        env = {
            key: os.environ[key]
            for key in ("SystemRoot", "WINDIR", "PATH", "LANG", "TZ")
            if key in os.environ
        }
        env.update({
            "HOME": str(profile), "USERPROFILE": str(profile),
            "APPDATA": str(profile), "LOCALAPPDATA": str(profile),
            "TEMP": str(profile), "TMP": str(profile), "TMPDIR": str(profile),
            "IWXA_OBSCURA_ALLOWED_ORIGIN": "https://example.com",
        })
        creation = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        probe = subprocess.run(
            [str(target), "--iwxa-attest-origin-guard"], check=True,
            capture_output=True, timeout=20, env=env, **creation,
        )
        assert probe.stdout.strip() == b"IWXA_ORIGIN_NET_GUARD_V1_NO_RENDER", probe.stdout[:120]
        launched = time.monotonic()
        process = subprocess.Popen(
            [str(target)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=profile, env=env, **creation,
        )
        try:
            require_worker_greeting(process.stdout, deadline=time.monotonic() + 30)
            greeting_ms = int((time.monotonic() - launched) * 1000)
            response, error = process.communicate(
                input=b'{"cmd":"shutdown"}\n', timeout=20,
            )
            assert process.returncode == 0, (process.returncode, error[-1000:])
            assert any(item.get("result") == "bye" for item in map(
                json.loads, response.decode("utf-8").splitlines()
            )), response[-500:]
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)

        # Corrupt the newer release and prove that readback fails without
        # deleting or implicitly rolling back a previously installed version.
        os.chmod(newer.executable, 0o600)
        Path(newer.executable).write_bytes(b"damaged CI version")
        damaged = backend.recover(updated, str(install_root))
        assert damaged.status == "INSTALL_MISMATCH" and not damaged.installed
        assert backend.recover(spec, str(install_root)) == installed
        try:
            backend.apply(updated, str(staging_root), updated_approval)
            raise AssertionError("damaged install unexpectedly overwritten")
        except InstallError as error:
            assert error.code == "INSTALL_TARGET_CONFLICT", error.code

        # Removal happens only after the native Worker has shut down.
        # A broken update can be removed independently of the working version.
        removal = ApprovedRemoval(
            program_id=backend.program_id,
            spec_fingerprint=updated.fingerprint,
            receipt_id="PINS-ci-remove-" + native_sha[:16],
            installation_root=str(install_root), confirmed=True,
        )
        removed = backend.remove(updated, removal)
        assert removed.status == "REMOVED" and not removed.installed
        assert backend.recover(updated, str(install_root)).status == "NOT_INSTALLED"
        assert backend.recover(spec, str(install_root)) == installed
        assert backend.remove(updated, removal).status == "NOT_INSTALLED"
        # Then remove the previously running native version, without deleting
        # the owning application directory or the staged executable.
        old_removal = ApprovedRemoval(
            program_id=backend.program_id,
            spec_fingerprint=spec.fingerprint,
            receipt_id="PINS-ci-remove-old-" + native_sha[:16],
            installation_root=str(install_root), confirmed=True,
        )
        assert backend.remove(spec, old_removal).status == "REMOVED"
        assert backend.recover(spec, str(install_root)).status == "NOT_INSTALLED"
        assert Path(prepared.executable).exists()
        assert install_root.exists()

        report = {
            "schema": "IWXA_UNIVERSAL_NATIVE_INSTALL_CI/1.0",
            "platform": platform,
            "source_commit": UPSTREAM_COMMIT,
            "patch_sha256": patch_sha,
            "build_binary_sha256": native_sha,
            "archive_sha256": archive_sha,
            "installed_binary_sha256": native_sha,
            "new_version_removed_after_corruption": removed.status == "REMOVED",
            "old_version_survived_new_version_removal": True,
            "old_version_removed_after_worker_shutdown": True,
            "prepared_status": prepared.status,
            "installed_status": installed.status,
            "update_status": newer.status,
            "updated_version_corruption_detected": damaged.status == "INSTALL_MISMATCH",
            "old_version_unaffected_by_corrupt_update": True,
            "unauthorized_install_rejected": True,
            "connected": installed.connected,
            "receipt_written_by_module": installed.receipt_written,
            "fresh_process_greeting": GUARD_GREETING.decode("ascii").strip(),
            "greeting_elapsed_ms": greeting_ms,
            "native_attestation": probe.stdout.decode("ascii").strip(),
            "real_native_binary": True,
            "published_release": False,
            "tool_runtime_connected": False,
            "duration_ms": int((time.monotonic() - start) * 1000),
            "status": "PASS",
        }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
