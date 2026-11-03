"""Public IWXA-managed Obscura Worker backend used by embedding products.

This backend installs pinned bytes into a host-selected *versioned* directory.
It never starts the worker, activates a provider, creates a receipt, or changes
another installed version. Host authorization is REQUIRED before apply().
"""
from __future__ import annotations

import hashlib
import json
import os
import platform as host_platform
from pathlib import Path
from typing import Callable
import sys
import re
import stat

from .contract import ApprovedInstall, ApprovedRemoval, InstallError, InstallObservation
from iwxa_obscura.preparer import (
    ArtifactPreparationError, ArtifactSpec, ObscuraArtifactPreparer,
    _check_parent_chain, _sha_file, BINARY_LIMIT,
)


PROGRAM_ID = "OBSCURA_WORKER_V1"
FAMILY = "IWXA_MANAGED_ARTIFACT_V1"
_CHUNK = 1024 * 1024
_INSTALL_MARKER = ".iwxa-installed.json"
_INSTALL_MARKER_SCHEMA = "IWXA_MANAGED_BINARY/1"


def _marker_contents(spec: ArtifactSpec) -> dict:
    return {
        "schema": _INSTALL_MARKER_SCHEMA,
        "program_id": PROGRAM_ID,
        "spec_fingerprint": spec.fingerprint,
    }


def _owned_marker(directory: Path, spec: ArtifactSpec) -> bool | None:
    """True=matching owner, False=invalid owner, None=legacy/no marker."""
    marker = directory / _INSTALL_MARKER
    try:
        _check_parent_chain(marker)
    except ArtifactPreparationError as exc:
        raise InstallError(exc.code) from None
    if not marker.exists():
        return None
    try:
        if not marker.is_file() or marker.stat().st_size > 8192:
            return False
        return json.loads(marker.read_text(encoding="utf-8")) == _marker_contents(spec)
    except (OSError, UnicodeError, ValueError):
        return False


def _native_platform() -> str:
    arch = host_platform.machine().lower()
    if arch in ("x86_64", "amd64"):
        arch = "x86_64"
    elif arch in ("aarch64", "arm64"):
        arch = "arm64"
    else:
        raise InstallError("INSTALL_PLATFORM_UNSUPPORTED")
    if os.name == "nt":
        family = "windows"
    elif sys.platform == "linux":
        family = "linux"
    elif sys.platform == "darwin":
        family = "macos"
    else:
        raise InstallError("INSTALL_PLATFORM_UNSUPPORTED")
    return f"{family}-{arch}"


def _require_host_platform(spec: ArtifactSpec) -> None:
    if not isinstance(spec, ArtifactSpec):
        raise InstallError("INSTALL_SPEC_INVALID")
    if spec.platform != _native_platform():
        raise InstallError("INSTALL_PLATFORM_MISMATCH")


def _root(value: str) -> Path:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise InstallError("INSTALL_ROOT_INVALID")
    root = Path(value)
    if not root.is_absolute() or root == Path(root.anchor) or ".." in root.parts:
        raise InstallError("INSTALL_ROOT_INVALID")
    try:
        _check_parent_chain(root)
    except ArtifactPreparationError as exc:
        raise InstallError(exc.code) from None
    if root.exists() and not root.is_dir():
        raise InstallError("INSTALL_ROOT_INVALID")
    return root


class ObscuraManagedBackend:
    """One backend implementation; the host still owns the installer workflow."""

    family = FAMILY
    program_id = PROGRAM_ID

    @staticmethod
    def _target(spec: ArtifactSpec, installation_root: str) -> Path:
        if not isinstance(spec, ArtifactSpec):
            raise InstallError("INSTALL_SPEC_INVALID")
        return _root(installation_root) / "obscura-worker" / spec.release_id / spec.platform

    def inspect(self, spec: ArtifactSpec, installation_root: str) -> InstallObservation:
        return self.verify(spec, installation_root)

    def prepare(
        self, spec: ArtifactSpec, staging_root: str, *,
        archive_path: str | None = None,
        on_progress: Callable[[int, int | None], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ):
        """Stage pinned bytes, with optional progress/cancel for HTTPS downloads.

        A local archive is an already completed transfer. The host still
        decides when to stage, install, connect or stop a Worker.
        """
        if not isinstance(spec, ArtifactSpec):
            raise InstallError("INSTALL_SPEC_INVALID")
        root = _root(staging_root)
        if is_cancelled is not None:
            if not callable(is_cancelled):
                raise InstallError("INSTALL_CANCEL_CALLBACK_INVALID")
            if is_cancelled():
                raise InstallError("ARTIFACT_DOWNLOAD_CANCELLED")
        preparer = ObscuraArtifactPreparer(managed_root=root)
        if archive_path is None:
            # Download is an explicit request from the host, not a background task.
            return preparer.download_and_prepare(
                spec, on_progress=on_progress, is_cancelled=is_cancelled)
        result = preparer.prepare_local(archive_path, spec)
        if on_progress is not None:
            on_progress(1, 1)
        return result

    def verify(self, spec: ArtifactSpec, installation_root: str) -> InstallObservation:
        """Read-only disk verification; no implicit repair or activation."""
        _require_host_platform(spec)
        directory = self._target(spec, installation_root)
        executable = directory / spec.executable_name
        try:
            _check_parent_chain(directory)
        except ArtifactPreparationError as exc:
            raise InstallError(exc.code) from None
        if not directory.exists():
            return InstallObservation("NOT_INSTALLED", PROGRAM_ID)
        if not directory.is_dir() or not executable.is_file():
            return InstallObservation("INSTALL_INCOMPLETE", PROGRAM_ID)
        if _owned_marker(directory, spec) is False:
            return InstallObservation("INSTALL_MISMATCH", PROGRAM_ID)
        try:
            digest = _sha_file(executable, BINARY_LIMIT)
        except (ArtifactPreparationError, OSError):
            return InstallObservation("INSTALL_MISMATCH", PROGRAM_ID)
        if digest != spec.executable_sha256:
            return InstallObservation("INSTALL_MISMATCH", PROGRAM_ID)
        # A matching checksum alone is not enough on POSIX: users can remove
        # execute permission after installation. Readback must not claim that
        # an unlaunchable binary is an installed, usable Worker.
        if os.name != "nt":
            try:
                mode = executable.stat().st_mode
                if not (mode & 0o111) or not os.access(executable, os.X_OK):
                    return InstallObservation("INSTALL_NOT_EXECUTABLE", PROGRAM_ID)
            except OSError:
                return InstallObservation("INSTALL_NOT_EXECUTABLE", PROGRAM_ID)
        return InstallObservation(
            "INSTALLED_NOT_CONNECTED", PROGRAM_ID, executable=str(executable),
            sha256=digest, installed=True,
        )

    def recover(self, spec: ArtifactSpec, installation_root: str) -> InstallObservation:
        """After a crash only observe; never download, copy or rerun apply."""
        return self.verify(spec, installation_root)

    def remove(self, spec: ArtifactSpec, approval: ApprovedRemoval) -> InstallObservation:
        """Remove only the pinned Worker version after the host stops its use.

        Never remove parent roots, staged files, adjacent releases, receipts, or
        foreign files. Interrupted and hash-mismatched versions may be removed,
        but unexpected contents and reparse points are never recursively erased.
        The host must serialize install/remove and persist its own lifecycle.
        """
        if not isinstance(approval, ApprovedRemoval) or approval.confirmed is not True:
            raise InstallError("REMOVE_OWNER_APPROVAL_REQUIRED")
        if (approval.program_id != PROGRAM_ID or
                approval.spec_fingerprint != getattr(spec, "fingerprint", None) or
                not isinstance(approval.receipt_id, str) or
                re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", approval.receipt_id) is None):
            raise InstallError("REMOVE_APPROVAL_MISMATCH")
        _require_host_platform(spec)
        directory = self._target(spec, approval.installation_root)
        executable = directory / spec.executable_name
        try:
            _check_parent_chain(directory)
            _check_parent_chain(executable)
        except ArtifactPreparationError as exc:
            raise InstallError(exc.code) from None
        if not directory.exists():
            return InstallObservation("NOT_INSTALLED", PROGRAM_ID)
        if not directory.is_dir():
            raise InstallError("REMOVE_TARGET_INVALID")
        try:
            entries = list(directory.iterdir())
            names = {entry.name for entry in entries}
            if names - {spec.executable_name, _INSTALL_MARKER}:
                raise InstallError("REMOVE_FOREIGN_FILES_PRESENT")
            ownership = _owned_marker(directory, spec)
            if ownership is False:
                raise InstallError("REMOVE_OWNERSHIP_UNKNOWN")
            if spec.executable_name in names:
                info = executable.lstat()
                if not stat.S_ISREG(info.st_mode):
                    raise InstallError("REMOVE_TARGET_INVALID")
                if ownership is None:
                    # Pre-marker installs are compatible only when their
                    # binary still matches the pinned release. An unknown
                    # damaged file must not be deleted as if it were ours.
                    try:
                        legacy_digest = _sha_file(executable, BINARY_LIMIT)
                    except ArtifactPreparationError:
                        raise InstallError("REMOVE_OWNERSHIP_UNKNOWN") from None
                    if legacy_digest != spec.executable_sha256:
                        raise InstallError("REMOVE_OWNERSHIP_UNKNOWN")
                executable.unlink()
            if ownership is True:
                (directory / _INSTALL_MARKER).unlink()
            directory.rmdir()
        except InstallError:
            raise
        except FileNotFoundError:
            # A concurrent remover or filesystem race is not proof of success.
            raise InstallError("REMOVE_OUTCOME_UNKNOWN") from None
        except OSError:
            raise InstallError("REMOVE_BLOCKED_RECHECK_REQUIRED") from None
        return InstallObservation("REMOVED", PROGRAM_ID)

    def apply(self, spec: ArtifactSpec, staging_root: str,
              approval: ApprovedInstall) -> InstallObservation:
        """Install one pinned binary into a new, host-owned version directory.

        The host must persist STARTED and serialize installs before this call.
        The resulting file is NOT connected or executed. No rollback API.
        """
        if not isinstance(approval, ApprovedInstall) or approval.confirmed is not True:
            raise InstallError("INSTALL_OWNER_APPROVAL_REQUIRED")
        if (approval.program_id != PROGRAM_ID or
                approval.spec_fingerprint != getattr(spec, "fingerprint", None) or
                not isinstance(approval.receipt_id, str) or
                re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", approval.receipt_id) is None):
            raise InstallError("INSTALL_APPROVAL_MISMATCH")
        _require_host_platform(spec)
        stage_root = _root(staging_root)
        installation_root = _root(approval.installation_root)
        # Prevent nested software/staging roots and partial releases in a
        # user's product directory. The owning application selects both.
        stage_path = stage_root.resolve()
        install_path = installation_root.resolve()
        if (stage_path == install_path or stage_path in install_path.parents
                or install_path in stage_path.parents):
            raise InstallError("INSTALL_STAGING_EQUALS_TARGET")
        if not stage_root.is_dir():
            raise InstallError("INSTALL_PREPARED_UNAVAILABLE")
        target = self._target(spec, str(installation_root))
        if target.exists() or target.is_symlink():
            existing = self.verify(spec, approval.installation_root)
            if existing.installed:
                return existing
            raise InstallError("INSTALL_TARGET_CONFLICT")
        try:
            prepared = ObscuraArtifactPreparer(managed_root=stage_root).verify_prepared(spec)
            source = Path(prepared.executable)
            _check_parent_chain(source)
            if source.resolve() == (target / spec.executable_name).resolve():
                raise InstallError("INSTALL_STAGING_EQUALS_TARGET")
            parent = target.parent
            _check_parent_chain(parent)
            parent.mkdir(parents=True, exist_ok=True)
            # mkdir is an atomic no-overwrite gate, including on POSIX.
            target.mkdir()
        except InstallError:
            raise
        except FileExistsError:
            raise InstallError("INSTALL_TARGET_CONFLICT") from None
        except ArtifactPreparationError as exc:
            raise InstallError(exc.code) from None
        except OSError:
            raise InstallError("INSTALL_DESTINATION_UNAVAILABLE") from None
        destination = target / spec.executable_name
        try:
            # Persist the version owner BEFORE copying potentially large
            # executable bytes. A hard crash during the copy must leave a
            # recognizable, removable partial IWXA-managed installation.
            # This is local ownership metadata, not a lifecycle receipt.
            with (target / _INSTALL_MARKER).open("x", encoding="utf-8") as sink:
                json.dump(_marker_contents(spec), sink, sort_keys=True, separators=(",", ":"))
                sink.flush()
                os.fsync(sink.fileno())
            sha = hashlib.sha256()
            size = 0
            with source.open("rb") as inp, destination.open("xb") as out:
                while True:
                    block = inp.read(_CHUNK)
                    if not block:
                        break
                    size += len(block)
                    if size > BINARY_LIMIT:
                        raise InstallError("INSTALL_SIZE_LIMIT")
                    sha.update(block)
                    out.write(block)
                out.flush()
                os.fsync(out.fileno())
            if sha.hexdigest() != spec.executable_sha256:
                raise InstallError("INSTALL_INPUT_CHANGED")
            if os.name != "nt":
                destination.chmod(0o500)
            observation = self.verify(spec, approval.installation_root)
            if not observation.installed:
                raise InstallError("INSTALL_VERIFY_FAILED")
            return observation
        except (OSError, InstallError) as exc:
            try:
                (target / _INSTALL_MARKER).unlink(missing_ok=True)
                destination.unlink(missing_ok=True)
                target.rmdir()
            except OSError:
                pass
            raise InstallError(getattr(exc, "code", "INSTALL_WRITE_FAILED")) from None
