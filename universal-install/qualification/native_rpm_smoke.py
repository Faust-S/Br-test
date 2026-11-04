"""Real RPM package install/remove on disposable Ubuntu GitHub-hosted runners.

The RPM database and only one CI-owned /usr/share file are touched.
Must run under sudo on an ephemeral runner with rpm and rpmbuild installed.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "universal-install"))
from iwxa_universal_install import (
    ApprovedInstall, ApprovedRemoval, ManagedArtifactSpec,
    NativePackageBackend, NativePackageSpec
)
from iwxa_universal_install.managed import _native_platform

PACKAGE_ID = "iwxa-native-rpm-ci-fixture"
PAYLOAD = Path("/usr/share/iwxa-native-rpm-ci-fixture/hello.txt")


def run(*argv):
    return subprocess.run(argv, capture_output=True, text=True, timeout=180, check=True)


def installed():
    return subprocess.run(["rpm", "-q", "--quiet", PACKAGE_ID],
                          timeout=20, check=False).returncode == 0


def qualify():
    if not sys.platform.startswith("linux") or _native_platform() not in ("linux-x86_64", "linux-arm64"):
        raise RuntimeError("RPM_SMOKE_LINUX_ONLY")
    for tool in ("rpm", "rpmbuild"):
        if not shutil.which(tool):
            raise RuntimeError("RPM_SMOKE_TOOL_UNAVAILABLE:" + tool)
    if installed():
        raise RuntimeError("RPM_SMOKE_PACKAGE_ALREADY_INSTALLED")
    with tempfile.TemporaryDirectory(prefix="iwxa-native-rpm-ci-") as tmp:
        root = Path(tmp).resolve()
        top = root / "rpmbuild"
        for name in ("BUILD", "BUILDROOT", "RPMS", "SOURCES", "SPECS", "SRPMS"):
            (top / name).mkdir(parents=True)
        specfile = top / "SPECS" / (PACKAGE_ID + ".spec")
        specfile.write_text(
            """Name: iwxa-native-rpm-ci-fixture
Version: 1.0.0
Release: 1
Summary: Disposable IWXA native installer test
License: MIT
BuildArch: noarch
%description
Disposable CI-only installed file
%prep
%build
%install
mkdir -p %{buildroot}/usr/share/iwxa-native-rpm-ci-fixture
printf 'verified native rpm fixture\\n' > %{buildroot}/usr/share/iwxa-native-rpm-ci-fixture/hello.txt
%files
/usr/share/iwxa-native-rpm-ci-fixture/hello.txt
""", encoding="utf-8"
        )
        run("rpmbuild", "--define", "_topdir " + str(top), "-bb", str(specfile))
        packages = list((top / "RPMS").rglob("*.rpm"))
        if len(packages) != 1:
            raise RuntimeError("RPM_SMOKE_BUILD_INVALID")
        pkg = packages[0]
        sha = hashlib.sha256(pkg.read_bytes()).hexdigest()
        archive = ManagedArtifactSpec(
            program_id="NATIVE_RPM_CI_V1", slug="native-rpm-ci",
            release_id="1.0.0", platform=_native_platform(),
            executable_name=pkg.name, archive_name=pkg.name,
            archive_format="rpm", archive_sha256=sha,
            executable_sha256=sha,
            download_url="https://github.com/Faust-S/Br-test/releases/download/1.0.0/" + pkg.name
        )
        spec = NativePackageSpec(archive, PACKAGE_ID, installed_version="1.0.0-1")
        backend = NativePackageBackend()
        stage = root / "staging"
        owned = root / "owner"
        owned.mkdir()
        userfile = owned / "user-data"
        userfile.write_text("preserve", encoding="utf-8")
        try:
            backend.prepare(spec, str(stage), archive_path=str(pkg))
            approval = ApprovedInstall(
                spec.program_id, spec.fingerprint, "PINS-native-rpm-install", str(owned), True)
            if not backend.apply(spec, str(stage), approval).installed:
                raise RuntimeError("RPM_SMOKE_APPLY_INVALID")
            if not installed() or not PAYLOAD.is_file():
                raise RuntimeError("RPM_SMOKE_INSTALL_READBACK_INVALID")
            if not backend.recover(spec, str(owned)).installed:
                raise RuntimeError("RPM_SMOKE_RECOVER_INVALID")
            # Upgrade the very same system-owned package identity.
            specfile.write_text(
                specfile.read_text(encoding="utf-8").replace(
                    "Version: 1.0.0", "Version: 1.1.0"), encoding="utf-8")
            run("rpmbuild", "--define", "_topdir " + str(top), "-bb", str(specfile))
            matches = list((top / "RPMS").rglob("*1.1.0*.rpm"))
            if len(matches) != 1:
                raise RuntimeError("RPM_SMOKE_UPGRADE_BUILD_INVALID")
            second_package = matches[0]
            next_pin = hashlib.sha256(second_package.read_bytes()).hexdigest()
            next_artifact = ManagedArtifactSpec(
                program_id=archive.program_id, slug=archive.slug, release_id="1.1.0",
                platform=archive.platform, executable_name=second_package.name,
                archive_name=second_package.name, archive_format="rpm",
                archive_sha256=next_pin, executable_sha256=next_pin,
                download_url="https://github.com/Faust-S/Br-test/releases/download/1.1.0/"
                + second_package.name)
            next_spec = NativePackageSpec(next_artifact, PACKAGE_ID,
                                          installed_version="1.1.0-1")
            backend.prepare(next_spec, str(stage), archive_path=str(second_package))
            update = ApprovedInstall(
                next_spec.program_id, next_spec.fingerprint,
                "PINS-native-rpm-upgrade", str(owned), True)
            if not backend.apply(next_spec, str(stage), update).installed:
                raise RuntimeError("RPM_SMOKE_UPGRADE_FAILED")
            if backend.verify(spec, str(owned)).installed:
                raise RuntimeError("RPM_SMOKE_OLD_VERSION_FALSE_POSITIVE")
            old_remove = ApprovedRemoval(spec.program_id, spec.fingerprint,
                                         "PINS-native-rpm-old-remove", str(owned), True)
            if backend.remove(spec, old_remove).status != "NOT_INSTALLED":
                raise RuntimeError("RPM_SMOKE_OLD_VERSION_REMOVE_NOT_BLOCKED")
            if not backend.verify(next_spec, str(owned)).installed:
                raise RuntimeError("RPM_SMOKE_NEW_VERSION_NOT_PRESERVED")
            removal = ApprovedRemoval(
                next_spec.program_id, next_spec.fingerprint,
                "PINS-native-rpm-remove", str(owned), True)
            if backend.remove(next_spec, removal).status != "REMOVED":
                raise RuntimeError("RPM_SMOKE_REMOVE_INVALID")
            if installed() or PAYLOAD.exists():
                raise RuntimeError("RPM_SMOKE_REMOVE_READBACK_INVALID")
            if userfile.read_text(encoding="utf-8") != "preserve":
                raise RuntimeError("RPM_SMOKE_USER_DATA_CHANGED")
            report = {"schema": "IWXA_NATIVE_RPM_SMOKE/1", "status": "PASS",
                      "platform": _native_platform(), "installed": True,
                      "verified": True, "removed": True, "upgraded": True,
                      "old_version_protected": True, "user_data_preserved": True}
            print(json.dumps(report, sort_keys=True))
            return report
        finally:
            if installed():
                run("rpm", "-e", PACKAGE_ID)


if __name__ == "__main__":
    qualify()
