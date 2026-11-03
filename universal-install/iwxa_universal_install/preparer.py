"""Reusable one-file ZIP/tar.gz staged package preparation.

The input manifest is supplied by a trusted program catalog; downloaded bytes
never set their own pins. Stage only; the host controls approval and activation.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import ssl
import stat
import tarfile
import tempfile
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, HTTPSHandler, ProxyHandler
import zipfile

from .manifest import ManagedArtifactSpec

ARCHIVE_LIMIT = 150 * 1024 * 1024
BINARY_LIMIT = 350 * 1024 * 1024
_CHUNK = 1024 * 1024
_MARKER = "artifact-manifest.json"
_CDN_HOSTS = frozenset({"release-assets.githubusercontent.com", "objects.githubusercontent.com"})


class ManagedPreparationError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ManagedPreparedArtifact:
    executable: str
    executable_sha256: str
    release_id: str
    platform: str
    artifact_fingerprint: str
    status: str = "STAGED_NOT_ACTIVATED"


def _check_parent_chain(path: Path) -> None:
    for item in (path, *path.parents):
        if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
            raise ManagedPreparationError("ARTIFACT_PATH_REPARSE_DENIED")


def _sha_file(path: Path, limit: int) -> str:
    _check_parent_chain(path)
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
            raise ManagedPreparationError("ARTIFACT_INPUT_NOT_REGULAR")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(_CHUNK):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        raise ManagedPreparationError("ARTIFACT_INPUT_UNREADABLE") from None


def _write_limited(source, target: Path):
    size = 0
    with target.open("xb") as output:
        while True:
            chunk = source.read(_CHUNK)
            if not chunk:
                break
            size += len(chunk)
            if size > BINARY_LIMIT:
                raise ManagedPreparationError("ARTIFACT_EXECUTABLE_TOO_LARGE")
            output.write(chunk)
        output.flush()
        os.fsync(output.fileno())
    if not size:
        raise ManagedPreparationError("ARTIFACT_EXECUTABLE_EMPTY")


def _extract(archive: Path, destination: Path, spec: ManagedArtifactSpec) -> None:
    try:
        if spec.archive_format == "binary":
            # Official standalone executables need no repackaging.
            # Both binary and downloaded artifact digests are still pinned.
            with archive.open("rb") as source:
                _write_limited(source, destination)
        elif spec.archive_format == "zip":
            with zipfile.ZipFile(archive) as package:
                entries = package.infolist()
                if len(entries) != 1:
                    raise ManagedPreparationError("ARTIFACT_ARCHIVE_CONTENTS_INVALID")
                item = entries[0]
                if (item.filename != spec.executable_name or item.is_dir() or
                        stat.S_ISLNK(item.external_attr >> 16) or
                        not 0 < item.file_size <= BINARY_LIMIT):
                    raise ManagedPreparationError("ARTIFACT_ARCHIVE_CONTENTS_INVALID")
                with package.open(item) as source:
                    _write_limited(source, destination)
        else:
            with tarfile.open(archive, mode="r:gz") as package:
                item = package.next()
                if (item is None or item.name != spec.executable_name or not item.isfile()
                        or not 0 < item.size <= BINARY_LIMIT):
                    raise ManagedPreparationError("ARTIFACT_ARCHIVE_CONTENTS_INVALID")
                source = package.extractfile(item)
                if source is None:
                    raise ManagedPreparationError("ARTIFACT_ARCHIVE_INVALID")
                with source:
                    _write_limited(source, destination)
                if package.next() is not None:
                    raise ManagedPreparationError("ARTIFACT_ARCHIVE_CONTENTS_INVALID")
    except (OSError, EOFError, zipfile.BadZipFile, tarfile.TarError):
        raise ManagedPreparationError("ARTIFACT_ARCHIVE_INVALID") from None


class _ReleaseRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            url = urlsplit(newurl)
            valid = (url.scheme == "https" and url.hostname in _CDN_HOSTS
                     and url.port in (None, 443) and not url.username and not url.password)
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise ManagedPreparationError("ARTIFACT_DOWNLOAD_REDIRECT_DENIED")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class ManagedArtifactPreparer:
    """Reusable staging for one pinned executable from a ZIP or tar.gz."""

    def __init__(self, *, managed_root: str | Path):
        root = Path(managed_root)
        if not root.is_absolute() or root == Path(root.anchor) or ".." in root.parts:
            raise ValueError("absolute managed staging root required")
        _check_parent_chain(root)
        if root.exists() and not root.is_dir():
            raise ManagedPreparationError("ARTIFACT_ROOT_INVALID")
        root.mkdir(parents=True, exist_ok=True)
        self.root = root

    def _target(self, spec: ManagedArtifactSpec) -> Path:
        return self.root / spec.slug / spec.release_id / spec.platform

    def verify_prepared(self, spec: ManagedArtifactSpec) -> ManagedPreparedArtifact:
        if not isinstance(spec, ManagedArtifactSpec):
            raise ManagedPreparationError("MANAGED_SPEC_INVALID")
        folder = self._target(spec)
        executable = folder / spec.executable_name
        marker = folder / _MARKER
        _check_parent_chain(executable)
        _check_parent_chain(marker)
        try:
            if not folder.is_dir() or not executable.is_file() or not marker.is_file():
                raise ManagedPreparationError("ARTIFACT_PREPARED_MISSING")
            if marker.stat().st_size > 8192:
                raise ManagedPreparationError("ARTIFACT_PREPARED_MARKER_INVALID")
            data = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            raise ManagedPreparationError("ARTIFACT_PREPARED_MARKER_INVALID") from None
        if data != {"schema": "IWXA_MANAGED_STAGING/1", "spec_fingerprint": spec.fingerprint}:
            raise ManagedPreparationError("ARTIFACT_PREPARED_MARKER_INVALID")
        if _sha_file(executable, BINARY_LIMIT) != spec.executable_sha256:
            raise ManagedPreparationError("ARTIFACT_PREPARED_DIGEST_MISMATCH")
        return ManagedPreparedArtifact(str(executable), spec.executable_sha256,
                                       spec.release_id, spec.platform, spec.fingerprint)

    def prepare_local(self, archive_path: str | Path,
                      spec: ManagedArtifactSpec) -> ManagedPreparedArtifact:
        if not isinstance(spec, ManagedArtifactSpec):
            raise ManagedPreparationError("MANAGED_SPEC_INVALID")
        archive = Path(archive_path)
        if _sha_file(archive, ARCHIVE_LIMIT) != spec.archive_sha256:
            raise ManagedPreparationError("ARTIFACT_ARCHIVE_DIGEST_MISMATCH")
        target = self._target(spec)
        parent = target.parent
        _check_parent_chain(parent)
        parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            return self.verify_prepared(spec)
        temporary = Path(tempfile.mkdtemp(prefix=".iwxa-staging-", dir=parent))
        try:
            binary = temporary / spec.executable_name
            _extract(archive, binary, spec)
            if _sha_file(binary, BINARY_LIMIT) != spec.executable_sha256:
                raise ManagedPreparationError("ARTIFACT_EXECUTABLE_DIGEST_MISMATCH")
            if os.name != "nt":
                binary.chmod(0o500)
            with (temporary / _MARKER).open("x", encoding="utf-8") as output:
                json.dump({"schema": "IWXA_MANAGED_STAGING/1",
                           "spec_fingerprint": spec.fingerprint}, output,
                          sort_keys=True, separators=(",", ":"))
                output.flush()
                os.fsync(output.fileno())
            try:
                os.rename(temporary, target)
            except FileExistsError:
                return self.verify_prepared(spec)
            except OSError:
                raise ManagedPreparationError("ARTIFACT_STAGE_PUBLISH_FAILED") from None
            return self.verify_prepared(spec)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def download_and_prepare(
        self, spec: ManagedArtifactSpec, *, timeout_seconds: float = 30,
        on_progress: Callable[[int, int | None], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> ManagedPreparedArtifact:
        if not isinstance(spec, ManagedArtifactSpec):
            raise ManagedPreparationError("MANAGED_SPEC_INVALID")
        if not isinstance(timeout_seconds, (float, int)) or not 1 <= timeout_seconds <= 60:
            raise ValueError("invalid timeout")
        if on_progress is not None and not callable(on_progress):
            raise ValueError("invalid progress callback")
        if is_cancelled is not None and not callable(is_cancelled):
            raise ValueError("invalid cancellation callback")
        if is_cancelled is not None and is_cancelled():
            raise ManagedPreparationError("ARTIFACT_DOWNLOAD_CANCELLED")
        opener = build_opener(ProxyHandler(), HTTPSHandler(context=ssl.create_default_context()),
                              _ReleaseRedirect())
        with tempfile.TemporaryDirectory(prefix=".iwxa-download-", dir=self.root) as folder:
            archive = Path(folder) / spec.archive_name
            try:
                with opener.open(Request(spec.download_url,
                                         headers={"User-Agent": "IWXA-Managed-Install/1.0"}),
                                 timeout=timeout_seconds) as response:
                    raw_size = getattr(response, "headers", {}).get("Content-Length")
                    try:
                        total = max(0, int(raw_size)) if raw_size is not None else None
                    except (ValueError, TypeError):
                        total = None
                    if total is not None and total > ARCHIVE_LIMIT:
                        raise ManagedPreparationError("ARTIFACT_DOWNLOAD_TOO_LARGE")
                    if on_progress is not None:
                        on_progress(0, total)
                    digest = hashlib.sha256()
                    amount = 0
                    with archive.open("xb") as output:
                        while True:
                            if is_cancelled is not None and is_cancelled():
                                raise ManagedPreparationError("ARTIFACT_DOWNLOAD_CANCELLED")
                            block = response.read(_CHUNK)
                            if not block:
                                break
                            amount += len(block)
                            if amount > ARCHIVE_LIMIT:
                                raise ManagedPreparationError("ARTIFACT_DOWNLOAD_TOO_LARGE")
                            digest.update(block)
                            output.write(block)
                            if on_progress is not None:
                                on_progress(amount, total)
                        output.flush()
                        os.fsync(output.fileno())
                    if digest.hexdigest() != spec.archive_sha256:
                        raise ManagedPreparationError("ARTIFACT_ARCHIVE_DIGEST_MISMATCH")
            except ManagedPreparationError:
                raise
            except HTTPError as exc:
                code = ("ARTIFACT_DOWNLOAD_NOT_FOUND" if exc.code == 404 else
                        "ARTIFACT_DOWNLOAD_ACCESS_DENIED" if exc.code in (401, 403) else
                        "ARTIFACT_DOWNLOAD_HTTP_FAILED")
                raise ManagedPreparationError(code) from None
            except (TimeoutError, URLError) as exc:
                reason = getattr(exc, "reason", exc)
                code = ("ARTIFACT_DOWNLOAD_TLS_FAILED" if isinstance(reason, ssl.SSLError) else
                        "ARTIFACT_DOWNLOAD_TIMEOUT" if isinstance(reason, TimeoutError) else
                        "ARTIFACT_DOWNLOAD_FAILED")
                raise ManagedPreparationError(code) from None
            except ssl.SSLError:
                raise ManagedPreparationError("ARTIFACT_DOWNLOAD_TLS_FAILED") from None
            except (OSError, ValueError):
                raise ManagedPreparationError("ARTIFACT_DOWNLOAD_FAILED") from None
            return self.prepare_local(archive, spec)
