"""Disposable real MSI install and removal using GitHub-hosted Windows 2022 WiX.

Only the CI-owned MSI GUID and its installed fixture file are touched.
This requires an elevated ephemeral Windows runner; never run on user systems.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from xml.sax.saxutils import quoteattr

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "universal-install"))
from iwxa_universal_install import (
    ApprovedInstall, ApprovedRemoval, ManagedArtifactSpec,
    NativePackageBackend, NativePackageSpec
)
from iwxa_universal_install.managed import _native_platform
from iwxa_universal_install.native import _probe

PRODUCT_CODE = "{BEE3B938-DC6D-49F7-8181-37B086EA7171}"
PRODUCT_CODE_V2 = "{C9D63E56-2AA8-45EB-B4C4-68E7E663E15E}"
UPGRADE_CODE = "{94E7F34A-0AA3-413F-8040-0D9818E83C51}"
COMPONENT_CODE = "{017EBA07-850F-45FD-B4B3-D95D7A536507}"


def wix_binary(name: str) -> Path:
    roots = []
    if os.environ.get("WIX"):
        roots.extend([Path(os.environ["WIX"]), Path(os.environ["WIX"]) / "bin"])
    roots.extend([
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "WiX Toolset v3.14" / "bin",
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "WiX Toolset v3.11" / "bin",
    ])
    for root in roots:
        path = root / name
        if path.is_file():
            return path
    raise RuntimeError("MSI_SMOKE_WIX_MISSING:" + name)


def run(*args, check=True):
    return subprocess.run(args, check=check, capture_output=True,
                          text=True, errors="replace", timeout=180)


def qualify():
    if sys.platform != "win32" or _native_platform() != "windows-x86_64":
        raise RuntimeError("MSI_SMOKE_WINDOWS_X64_ONLY")
    with tempfile.TemporaryDirectory(prefix="iwxa-native-msi-ci-") as tmp:
        root = Path(tmp).resolve()
        source = root / "native-msi-test-file.txt"
        source.write_text("verified native MSI fixture\n", encoding="utf-8")
        wxs = root / "fixture.wxs"
        wxs.write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<Wix xmlns="http://schemas.microsoft.com/wix/2006/wi">\n'
            '<Product Id="' + PRODUCT_CODE + '" Name="IWXA Native MSI CI Fixture" '
            'Language="1033" Version="1.0.0" Manufacturer="IWXA CI" '
            'UpgradeCode="' + UPGRADE_CODE + '">\n'
            '<Package InstallerVersion="200" Compressed="yes" InstallScope="perMachine" />\n'
            '<MediaTemplate EmbedCab="yes" />\n'
            '<Directory Id="TARGETDIR" Name="SourceDir">\n'
            '<Directory Id="ProgramFilesFolder">\n'
            '<Directory Id="INSTALLDIR" Name="IWXA-Native-MSI-CI-Fixture">\n'
            '<Component Id="CIComponent" Guid="' + COMPONENT_CODE + '">\n'
            '<File Id="CIFile" KeyPath="yes" Source=' + quoteattr(str(source)) + ' />\n'
            '</Component></Directory></Directory></Directory>\n'
            '<Feature Id="Complete" Title="IWXA Native MSI CI Fixture" Level="1">\n'
            '<ComponentRef Id="CIComponent" />\n'
            '</Feature></Product></Wix>\n',
            encoding="utf-8"
        )
        obj = root / "fixture.wixobj"
        package = root / "iwxa-native-msi-ci.msi"
        run(str(wix_binary("candle.exe")), "-nologo", "-out", str(obj), str(wxs))
        run(str(wix_binary("light.exe")), "-nologo", "-out", str(package), str(obj))
        sha = hashlib.sha256(package.read_bytes()).hexdigest()
        artifact = ManagedArtifactSpec(
            program_id="NATIVE_MSI_CI_V1", slug="native-msi-ci",
            release_id="1.0.0", platform=_native_platform(),
            executable_name=package.name, archive_name=package.name,
            archive_format="msi", archive_sha256=sha, executable_sha256=sha,
            download_url="https://github.com/Faust-S/Br-test/releases/download/1.0.0/" + package.name
        )
        spec = NativePackageSpec(artifact, PRODUCT_CODE, installed_version="1.0.0")
        backend = NativePackageBackend()
        stage = root / "staging"
        owned = root / "owner"
        owned.mkdir()
        userfile = owned / "unrelated-user-file.txt"
        userfile.write_text("preserve", encoding="utf-8")
        upgraded_spec = None
        if _probe(spec):
            raise RuntimeError("MSI_SMOKE_PREEXISTING_PRODUCT")
        try:
            backend.prepare(spec, str(stage), archive_path=str(package))
            approval = ApprovedInstall(
                spec.program_id, spec.fingerprint, "PINS-native-msi-install", str(owned), True)
            result = backend.apply(spec, str(stage), approval)
            if not result.installed or not backend.recover(spec, str(owned)).installed:
                raise RuntimeError("MSI_SMOKE_INSTALL_READBACK_FAILED")
            # Build a real WiX major upgrade: new ProductCode, unchanged
            # UpgradeCode and destination. Only the vendor installer owns the
            # upgrade; the generic backend executes the pinned MSI.
            source.write_text("verified native MSI fixture v2\n", encoding="utf-8")
            upgraded_wxs = root / "upgrade.wxs"
            updated_xml = (wxs.read_text(encoding="utf-8")
                           .replace(PRODUCT_CODE, PRODUCT_CODE_V2)
                           .replace('Version="1.0.0"', 'Version="1.1.0"')
                           .replace('<MediaTemplate EmbedCab="yes" />',
                                    '<MediaTemplate EmbedCab="yes" />\n'
                                    '<MajorUpgrade DowngradeErrorMessage="A newer version is installed." />'))
            upgraded_wxs.write_text(updated_xml, encoding="utf-8")
            upgraded_obj = root / "upgrade.wixobj"
            upgraded_package = root / "iwxa-native-msi-ci-v2.msi"
            run(str(wix_binary("candle.exe")), "-nologo", "-out", str(upgraded_obj), str(upgraded_wxs))
            run(str(wix_binary("light.exe")), "-nologo", "-out", str(upgraded_package), str(upgraded_obj))
            upgraded_sha = hashlib.sha256(upgraded_package.read_bytes()).hexdigest()
            upgraded_artifact = ManagedArtifactSpec(
                program_id=artifact.program_id, slug=artifact.slug,
                release_id="1.1.0", platform=artifact.platform,
                executable_name=upgraded_package.name, archive_name=upgraded_package.name,
                archive_format="msi", archive_sha256=upgraded_sha,
                executable_sha256=upgraded_sha,
                download_url="https://github.com/Faust-S/Br-test/releases/download/1.1.0/"
                + upgraded_package.name
            )
            upgraded_spec = NativePackageSpec(
                upgraded_artifact, PRODUCT_CODE_V2, installed_version="1.1.0")
            backend.prepare(upgraded_spec, str(stage), archive_path=str(upgraded_package))
            approved_upgrade = ApprovedInstall(
                upgraded_spec.program_id, upgraded_spec.fingerprint,
                "PINS-native-msi-upgrade", str(owned), True)
            if not backend.apply(upgraded_spec, str(stage), approved_upgrade).installed:
                raise RuntimeError("MSI_SMOKE_UPGRADE_INVALID")
            if backend.verify(spec, str(owned)).installed:
                raise RuntimeError("MSI_SMOKE_OLD_VERSION_FALSE_POSITIVE")
            old_remove = ApprovedRemoval(
                spec.program_id, spec.fingerprint,
                "PINS-native-msi-old-remove", str(owned), True)
            if backend.remove(spec, old_remove).status != "NOT_INSTALLED":
                raise RuntimeError("MSI_SMOKE_OLD_VERSION_REMOVE_NOT_BLOCKED")
            if not backend.verify(upgraded_spec, str(owned)).installed:
                raise RuntimeError("MSI_SMOKE_NEW_VERSION_DAMAGED")
            removal = ApprovedRemoval(
                upgraded_spec.program_id, upgraded_spec.fingerprint,
                "PINS-native-msi-remove", str(owned), True)
            if backend.remove(upgraded_spec, removal).status != "REMOVED":
                raise RuntimeError("MSI_SMOKE_REMOVE_INVALID")
            if _probe(spec) or _probe(upgraded_spec):
                raise RuntimeError("MSI_SMOKE_REMOVE_READBACK_FAILED")
            if userfile.read_text(encoding="utf-8") != "preserve":
                raise RuntimeError("MSI_SMOKE_USER_DATA_CHANGED")
            report = {
                "schema": "IWXA_NATIVE_MSI_SMOKE/1", "status": "PASS",
                "platform": _native_platform(), "installed": True,
                "verified": True, "removed": True, "upgraded": True,
                "old_version_protected": True, "user_data_preserved": True
            }
            print(json.dumps(report, sort_keys=True))
            return report
        finally:
            if upgraded_spec is not None and _probe(upgraded_spec):
                run("msiexec.exe", "/x", PRODUCT_CODE_V2, "/qn", "/norestart")
            if _probe(spec):
                run("msiexec.exe", "/x", PRODUCT_CODE, "/qn", "/norestart")


if __name__ == "__main__":
    qualify()
