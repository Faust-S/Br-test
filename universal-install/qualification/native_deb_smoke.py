"""Real dpkg lifecycle against a tiny disposable CI-owned .deb package.

Run only on an ephemeral Ubuntu GitHub Actions runner with sudo. No third
party software, no user-profile writes. All installed files are CI fixtures.
"""
from __future__ import annotations
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "universal-install"))
from iwxa_universal_install import (
    ApprovedInstall, ApprovedRemoval, ManagedArtifactSpec, NativePackageBackend,
    NativePackageSpec
)
from iwxa_universal_install.managed import _native_platform

PACKAGE_ID = "iwxa-native-dpkg-ci-fixture"
PAYLOAD = "/usr/share/iwxa-native-dpkg-ci-fixture/hello.txt"


def run(*command):
    return subprocess.run(command, capture_output=True, text=True, check=True, timeout=120)


def installed() -> bool:
    result = subprocess.run(["dpkg-query", "-W", "-f=${Status}", PACKAGE_ID],
                            capture_output=True, text=True, timeout=15)
    return result.returncode == 0 and result.stdout.strip() == "install ok installed"


def qualify() -> dict:
    if sys.platform != "linux" or _native_platform() not in ("linux-x86_64", "linux-arm64"):
        raise RuntimeError("DEB_SMOKE_LINUX_ONLY")
    if installed():
        raise RuntimeError("DEB_SMOKE_PACKAGE_ALREADY_INSTALLED")
    if not shutil.which("dpkg-deb") or not shutil.which("dpkg"):
        raise RuntimeError("DEB_SMOKE_SYSTEM_TOOL_UNAVAILABLE")
    backend = NativePackageBackend()
    with tempfile.TemporaryDirectory(prefix="iwxa-native-deb-ci-") as temp:
        root = Path(temp).resolve()
        package_root = root / "package-root"
        control = package_root / "DEBIAN"
        control.mkdir(parents=True)
        (control / "control").write_text(
            "Package: " + PACKAGE_ID + "\n"
            "Version: 1.0.0\n"
            "Section: utils\n"
            "Priority: optional\n"
            "Architecture: all\n"
            "Maintainer: IWXA CI <ci@example.invalid>\n"
            "Description: Disposable universal installer smoke fixture\n",
            encoding="utf-8"
        )
        target = package_root / PAYLOAD.lstrip("/")
        target.parent.mkdir(parents=True)
        target.write_text("verified native dpkg fixture\n", encoding="utf-8")
        pkg = root / "iwxa-native-ci-fixture.deb"
        run("dpkg-deb", "--build", str(package_root), str(pkg))
        pin = hashlib.sha256(pkg.read_bytes()).hexdigest()
        artifact = ManagedArtifactSpec(
            program_id="NATIVE_DPKG_CI", slug="native-dpkg-ci", release_id="1.0.0",
            platform=_native_platform(), executable_name=pkg.name,
            archive_name=pkg.name, archive_format="deb",
            archive_sha256=pin, executable_sha256=pin,
            download_url="https://github.com/Faust-S/Br-test/releases/download/1.0.0/" + pkg.name
        )
        spec = NativePackageSpec(artifact, PACKAGE_ID, installed_version="1.0.0")
        staging = root / "staging"
        install_root = root / "owner"
        install_root.mkdir()
        user_data = install_root / "user-data.txt"
        user_data.write_text("untouched", encoding="utf-8")
        try:
            backend.prepare(spec, str(staging), archive_path=str(pkg))
            approval = ApprovedInstall(spec.program_id, spec.fingerprint,
                                       "PINS-native-deb-ci-install", str(install_root), True)
            observation = backend.apply(spec, str(staging), approval)
            if not observation.installed or not installed() or not Path(PAYLOAD).is_file():
                raise RuntimeError("DEB_SMOKE_INSTALL_VERIFY_FAILED")
            if backend.recover(spec, str(install_root)).status != "INSTALLED_NOT_CONNECTED":
                raise RuntimeError("DEB_SMOKE_RECOVER_FAILED")
            removal = ApprovedRemoval(spec.program_id, spec.fingerprint,
                                      "PINS-native-deb-ci-remove", str(install_root), True)
            if backend.remove(spec, removal).status != "REMOVED":
                raise RuntimeError("DEB_SMOKE_REMOVE_FAILED")
            if installed() or Path(PAYLOAD).exists():
                raise RuntimeError("DEB_SMOKE_REMOVE_READBACK_FAILED")
            if user_data.read_text(encoding="utf-8") != "untouched":
                raise RuntimeError("DEB_SMOKE_USER_DATA_CHANGED")
            if not (staging / artifact.slug / artifact.release_id /
                    artifact.platform / artifact.executable_name).exists():
                raise RuntimeError("DEB_SMOKE_STAGING_NOT_PRESERVED")
            outcome = {"schema":"IWXA_NATIVE_DEB_SMOKE/1", "platform":_native_platform(),
                       "status":"PASS","native_tool":"dpkg","installed":True,
                       "verified":True,"removed":True,"user_data_preserved":True}
            print(json.dumps(outcome, sort_keys=True))
            return outcome
        finally:
            # Always clean up the disposable CI fixture even if an assertion
            # fails; no modification is made to other package identities.
            if installed():
                run("dpkg", "--purge", PACKAGE_ID)


if __name__ == "__main__":
    qualify()
