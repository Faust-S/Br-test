"""Real macOS PKG install/readback on disposable macOS GitHub runners.

macOS pkgutil --forget is NOT an uninstaller. This smoke test deliberately
verifies the backend's refusal to claim universal PKG removal, then cleans up
only CI-owned paths and the CI-owned receipt from the ephemeral runner.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "universal-install"))
from iwxa_universal_install import (
    ApprovedInstall, ApprovedRemoval, InstallError, ManagedArtifactSpec,
    NativePackageBackend, NativePackageSpec
)
from iwxa_universal_install.managed import _native_platform

PACKAGE_ID = "org.iwxa.native.pkg.ci.fixture"
PAYLOAD = Path("/usr/local/share/iwxa-native-pkg-ci-fixture/hello.txt")


def run(*argv, check=True):
    return subprocess.run(argv, capture_output=True, text=True, check=check, timeout=180)


def installed():
    return run("pkgutil", "--pkg-info", PACKAGE_ID, check=False).returncode == 0


def qualify():
    if sys.platform != "darwin":
        raise RuntimeError("PKG_SMOKE_MACOS_ONLY")
    if installed() or PAYLOAD.exists():
        raise RuntimeError("PKG_SMOKE_EXISTING_PACKAGE_OR_FILES")
    backend = NativePackageBackend()
    with tempfile.TemporaryDirectory(prefix="iwxa-native-pkg-ci-") as tmp:
        root = Path(tmp).resolve()
        payload_root = root / "payload"
        payload = payload_root / PAYLOAD.relative_to("/")
        payload.parent.mkdir(parents=True)
        payload.write_text("verified native macOS PKG fixture\n", encoding="utf-8")
        package = root / "iwxa-native-pkg-ci.pkg"
        run("pkgbuild", "--root", str(payload_root),
            "--identifier", PACKAGE_ID, "--version", "1.0.0", str(package))
        sha = hashlib.sha256(package.read_bytes()).hexdigest()
        artifact = ManagedArtifactSpec(
            program_id="NATIVE_PKG_CI_V1", slug="native-pkg-ci",
            release_id="1.0.0", platform=_native_platform(),
            executable_name=package.name, archive_name=package.name,
            archive_format="pkg", archive_sha256=sha, executable_sha256=sha,
            download_url="https://github.com/Faust-S/Br-test/releases/download/1.0.0/" + package.name
        )
        spec = NativePackageSpec(artifact, PACKAGE_ID)
        stage = root / "staging"
        owned = root / "owner"
        owned.mkdir()
        userfile = owned / "user-data"
        userfile.write_text("keep", encoding="utf-8")
        try:
            backend.prepare(spec, str(stage), archive_path=str(package))
            approval = ApprovedInstall(
                spec.program_id, spec.fingerprint, "PINS-native-pkg-install", str(owned), True)
            if not backend.apply(spec, str(stage), approval).installed:
                raise RuntimeError("PKG_SMOKE_APPLY_INVALID")
            if not installed() or PAYLOAD.read_text(encoding="utf-8") != "verified native macOS PKG fixture\n":
                raise RuntimeError("PKG_SMOKE_INSTALL_READBACK_INVALID")
            if not backend.recover(spec, str(owned)).installed:
                raise RuntimeError("PKG_SMOKE_RECOVER_FAILED")
            removal = ApprovedRemoval(
                spec.program_id, spec.fingerprint, "PINS-native-pkg-remove", str(owned), True)
            try:
                backend.remove(spec, removal)
                raise RuntimeError("PKG_SMOKE_UNSUPPORTED_REMOVE_MUST_FAIL")
            except InstallError as exc:
                if exc.code != "REMOVE_NATIVE_PKG_UNSUPPORTED":
                    raise
            if userfile.read_text(encoding="utf-8") != "keep":
                raise RuntimeError("PKG_SMOKE_USER_DATA_CHANGED")
            report = {
                "schema": "IWXA_NATIVE_PKG_SMOKE/1", "status": "PASS",
                "platform": _native_platform(), "installed": True,
                "verified": True, "removed_by_backend": False,
                "native_uninstall_supported": False,
                "explicit_uninstall_required": True, "user_data_preserved": True
            }
            print(json.dumps(report, sort_keys=True))
            return report
        finally:
            # Only the disposable package's own test file and receipt.
            if PAYLOAD.exists():
                PAYLOAD.unlink()
                try:
                    PAYLOAD.parent.rmdir()
                except OSError:
                    pass
            if installed():
                run("pkgutil", "--forget", PACKAGE_ID)


if __name__ == "__main__":
    qualify()
