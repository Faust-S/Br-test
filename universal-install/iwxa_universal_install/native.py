"""System-native package adapter for pinned MSI, DEB, RPM, PKG and vendor EXE.

This is not a second manager: the host still owns user confirmation, receipts,
serialization, elevated execution and selection of trusted package manifests.
All commands are argv arrays, never shells. No implicit downloads or retries.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Callable

from .contract import ApprovedInstall, ApprovedRemoval, InstallError, InstallObservation
from .managed import _native_platform, _root
from .manifest import ManagedArtifactSpec, NATIVE_FORMATS
from .preparer import ManagedArtifactPreparer, _check_parent_chain, _sha_file, NATIVE_LIMIT

NATIVE_FAMILY = "IWXA_NATIVE_PACKAGE_V1"
_PACKAGE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+:-]{0,199}\Z")
_GUID = re.compile(r"\{[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\}\Z")
_ARG_LIMIT = 32


@dataclass(frozen=True)
class NativePackageSpec:
    artifact: ManagedArtifactSpec
    package_id: str
    install_arguments: tuple[str, ...] = ()
    uninstall_arguments: tuple[str, ...] = ()
    probe_arguments: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.artifact, ManagedArtifactSpec) or self.artifact.archive_format not in NATIVE_FORMATS:
            raise ValueError("NATIVE_SPEC_FORMAT_INVALID")
        if not isinstance(self.package_id, str) or not _PACKAGE_ID.fullmatch(self.package_id):
            # MSI product codes use braces and have their own strict validation.
            if self.artifact.archive_format != "msi" or not isinstance(self.package_id, str) or not _GUID.fullmatch(self.package_id):
                raise ValueError("NATIVE_SPEC_PACKAGE_ID_INVALID")
        fmt = self.artifact.archive_format
        if fmt == "msi" and not _GUID.fullmatch(self.package_id):
            raise ValueError("NATIVE_SPEC_MSI_PRODUCT_CODE_INVALID")
        for name in ("install_arguments", "uninstall_arguments", "probe_arguments"):
            args = getattr(self, name)
            if not isinstance(args, tuple) or len(args) > _ARG_LIMIT or any(
                not isinstance(x, str) or not x or len(x) > 512 or "\0" in x
                for x in args
            ):
                raise ValueError("NATIVE_SPEC_ARGUMENTS_INVALID")
        if fmt != "exe-installer" and any(
            (self.install_arguments, self.uninstall_arguments, self.probe_arguments)
        ):
            raise ValueError("NATIVE_SPEC_UNEXPECTED_ARGUMENTS")
        if fmt == "exe-installer" and (not self.install_arguments or not self.probe_arguments):
            raise ValueError("NATIVE_SPEC_VENDOR_CONTRACT_REQUIRED")
        if fmt == "exe-installer":
            for cmd in (self.probe_arguments, self.uninstall_arguments):
                if cmd and (not Path(cmd[0]).is_absolute() or ".." in Path(cmd[0]).parts):
                    raise ValueError("NATIVE_SPEC_COMMAND_PATH_INVALID")

    @classmethod
    def from_mapping(cls, data: dict) -> "NativePackageSpec":
        """Load a new native package using data only, with no per-app class."""
        if not isinstance(data, dict) or set(data) - {
            "artifact", "package_id", "install_arguments",
            "uninstall_arguments", "probe_arguments"
        } or not {"artifact", "package_id"} <= set(data):
            raise ValueError("NATIVE_SPEC_FIELDS_INVALID")
        artifact = ManagedArtifactSpec.from_mapping(data["artifact"])
        arguments = {}
        for field in ("install_arguments", "uninstall_arguments", "probe_arguments"):
            values = data.get(field, [])
            if not isinstance(values, list):
                raise ValueError("NATIVE_SPEC_ARGUMENTS_INVALID")
            arguments[field] = tuple(values)
        return cls(artifact=artifact, package_id=data["package_id"], **arguments)

    def as_dict(self) -> dict:
        return {
            "artifact": self.artifact.as_dict(),
            "package_id": self.package_id,
            "install_arguments": list(self.install_arguments),
            "uninstall_arguments": list(self.uninstall_arguments),
            "probe_arguments": list(self.probe_arguments),
        }

    @property
    def fingerprint(self) -> str:
        import hashlib
        import json
        raw = json.dumps({
            "artifact": self.artifact.as_dict(),
            "package_id": self.package_id,
            "install_arguments": self.install_arguments,
            "uninstall_arguments": self.uninstall_arguments,
            "probe_arguments": self.probe_arguments,
        }, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(raw).hexdigest()

    @property
    def program_id(self) -> str:
        return self.artifact.program_id


def _require_native(spec: NativePackageSpec) -> None:
    if not isinstance(spec, NativePackageSpec):
        raise InstallError("INSTALL_SPEC_INVALID")
    if spec.artifact.platform != _native_platform():
        raise InstallError("INSTALL_PLATFORM_MISMATCH")


def _command(spec: NativePackageSpec, operation: str, package: Path | None = None) -> list[str]:
    fmt = spec.artifact.archive_format
    ident = spec.package_id
    if fmt == "msi":
        if operation == "install":
            return ["msiexec.exe", "/i", str(package), "/qn", "/norestart"]
        if operation == "remove":
            return ["msiexec.exe", "/x", ident, "/qn", "/norestart"]
    if fmt == "deb":
        return ["dpkg", "-i", str(package)] if operation == "install" else ["dpkg", "-r", ident]
    if fmt == "rpm":
        return ["rpm", "-Uvh", "--replacepkgs", str(package)] if operation == "install" else ["rpm", "-e", ident]
    if fmt == "pkg":
        if operation == "install":
            return ["installer", "-pkg", str(package), "-target", "/"]
        raise InstallError("REMOVE_NATIVE_PKG_UNSUPPORTED")
    if fmt == "exe-installer":
        if operation == "install":
            return [str(package), *spec.install_arguments]
        if not spec.uninstall_arguments:
            raise InstallError("REMOVE_VENDOR_COMMAND_REQUIRED")
        return list(spec.uninstall_arguments)
    raise InstallError("NATIVE_FORMAT_UNSUPPORTED")


def _run(args: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, capture_output=True, text=True, errors="replace",
                              timeout=timeout, shell=False, check=False)
    except FileNotFoundError:
        raise InstallError("NATIVE_SYSTEM_TOOL_UNAVAILABLE") from None
    except subprocess.TimeoutExpired:
        raise InstallError("NATIVE_OPERATION_TIMEOUT") from None
    except OSError:
        raise InstallError("NATIVE_OPERATION_LAUNCH_FAILED") from None


def _probe(spec: NativePackageSpec) -> bool:
    fmt = spec.artifact.archive_format
    if fmt == "msi":
        try:
            import winreg
            keypaths = [
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall" + chr(92) + spec.package_id,
                r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall" + chr(92) + spec.package_id,
            ]
            for key in keypaths:
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key):
                        return True
                except FileNotFoundError:
                    continue
            return False
        except ImportError:
            raise InstallError("NATIVE_PLATFORM_MISMATCH") from None
    if fmt == "deb":
        result = _run(["dpkg-query", "-W", "-f=${Status}", spec.package_id], 20)
        return result.returncode == 0 and result.stdout.strip() == "install ok installed"
    if fmt == "rpm":
        return _run(["rpm", "-q", "--quiet", spec.package_id], 20).returncode == 0
    if fmt == "pkg":
        return _run(["pkgutil", "--pkg-info", spec.package_id], 20).returncode == 0
    if fmt == "exe-installer":
        # Vendor-specific readback is compulsory; a successful exit code
        # from setup alone is never evidence of installation.
        return _run(list(spec.probe_arguments), 20).returncode == 0
    raise InstallError("NATIVE_FORMAT_UNSUPPORTED")


class NativePackageBackend:
    """System install adapter; standard package-manager ownership, no local registry."""

    family = NATIVE_FAMILY

    def inspect(self, spec: NativePackageSpec, installation_root: str) -> InstallObservation:
        return self.verify(spec, installation_root)

    def prepare(self, spec: NativePackageSpec, staging_root: str, *,
                archive_path: str | None = None,
                on_progress: Callable[[int, int | None], None] | None = None,
                is_cancelled: Callable[[], bool] | None = None):
        _require_native(spec)
        staging = ManagedArtifactPreparer(managed_root=_root(staging_root))
        if is_cancelled is not None:
            if not callable(is_cancelled):
                raise InstallError("INSTALL_CANCEL_CALLBACK_INVALID")
            if is_cancelled():
                raise InstallError("ARTIFACT_DOWNLOAD_CANCELLED")
        if archive_path is None:
            return staging.download_and_prepare(
                spec.artifact, on_progress=on_progress, is_cancelled=is_cancelled)
        staged = staging.prepare_local(archive_path, spec.artifact)
        if on_progress is not None:
            on_progress(1, 1)
        return staged

    def verify(self, spec: NativePackageSpec, installation_root: str) -> InstallObservation:
        _require_native(spec)
        _root(installation_root)
        installed = _probe(spec)
        return InstallObservation(
            "INSTALLED_NOT_CONNECTED" if installed else "NOT_INSTALLED",
            spec.program_id, source=NATIVE_FAMILY, installed=installed
        )

    def recover(self, spec: NativePackageSpec, installation_root: str) -> InstallObservation:
        return self.verify(spec, installation_root)

    def apply(self, spec: NativePackageSpec, staging_root: str, approval: ApprovedInstall) -> InstallObservation:
        _require_native(spec)
        if not isinstance(approval, ApprovedInstall) or approval.confirmed is not True:
            raise InstallError("INSTALL_OWNER_APPROVAL_REQUIRED")
        if approval.program_id != spec.program_id or approval.spec_fingerprint != spec.fingerprint:
            raise InstallError("INSTALL_APPROVAL_MISMATCH")
        _root(approval.installation_root)
        staged = ManagedArtifactPreparer(managed_root=_root(staging_root)).verify_prepared(spec.artifact)
        package = Path(staged.executable)
        _check_parent_chain(package)
        if _sha_file(package, NATIVE_LIMIT) != spec.artifact.executable_sha256:
            raise InstallError("INSTALL_STAGED_DIGEST_MISMATCH")
        command = _command(spec, "install", package)
        result = _run(command)
        if result.returncode not in ((0, 3010) if spec.artifact.archive_format == "msi" else (0,)):
            raise InstallError("NATIVE_INSTALL_FAILED")
        observation = self.verify(spec, approval.installation_root)
        if not observation.installed:
            raise InstallError("NATIVE_INSTALL_NOT_VERIFIED")
        if result.returncode == 3010:
            return InstallObservation("INSTALL_REBOOT_REQUIRED", spec.program_id,
                                      source=NATIVE_FAMILY, installed=True)
        return observation

    def remove(self, spec: NativePackageSpec, approval: ApprovedRemoval) -> InstallObservation:
        _require_native(spec)
        if not isinstance(approval, ApprovedRemoval) or approval.confirmed is not True:
            raise InstallError("REMOVE_OWNER_APPROVAL_REQUIRED")
        if approval.program_id != spec.program_id or approval.spec_fingerprint != spec.fingerprint:
            raise InstallError("REMOVE_APPROVAL_MISMATCH")
        _root(approval.installation_root)
        if not _probe(spec):
            return InstallObservation("NOT_INSTALLED", spec.program_id, source=NATIVE_FAMILY)
        result = _run(_command(spec, "remove"))
        if result.returncode not in ((0, 3010) if spec.artifact.archive_format == "msi" else (0,)):
            raise InstallError("NATIVE_REMOVE_FAILED")
        if _probe(spec):
            raise InstallError("NATIVE_REMOVE_NOT_VERIFIED")
        return InstallObservation("REMOVED", spec.program_id, source=NATIVE_FAMILY)
