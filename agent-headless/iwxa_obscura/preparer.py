"""IWXA Obscura artifact preparation, not a second installation authority.

The governing product owns approval, software roots, lifecycle receipts, binary
activation and rollback. This dependency-free library only downloads / verifies
an explicitly pinned release and prepares an immutable, inactive executable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import stat
import tarfile
import tempfile
from urllib.parse import urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPSHandler, ProxyHandler, HTTPRedirectHandler
import zipfile

SCHEMA = "IWXA_OBSCURA_WORKER_ARTIFACT/1.0.0"
UPSTREAM_COMMIT = "8534b401898ed17b62f69bdc3d5eb186bffb0d60"
GUARD_PROTOCOL = "IWXA_ORIGIN_NET_GUARD_V2_READY_NO_RENDER"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")
PLATFORMS = {
    "windows-x86_64": "obscura-worker.exe",
    "windows-arm64": "obscura-worker.exe",
    "linux-x86_64": "obscura-worker",
    "linux-arm64": "obscura-worker",
    "macos-x86_64": "obscura-worker",
    "macos-arm64": "obscura-worker",
}
ARCHIVE_LIMIT = 150 * 1024 * 1024
BINARY_LIMIT = 350 * 1024 * 1024
_CHUNK = 1024 * 1024
_GITHUB_REPO = "/Faust-S/Br-test/releases/download/"
_CDN_HOSTS = frozenset({"release-assets.githubusercontent.com", "objects.githubusercontent.com"})


class ArtifactPreparationError(RuntimeError):
    """Stable, content-free failure code safe for a product status display."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _is_symlink_or_reparse(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _check_parent_chain(path: Path) -> None:
    """Reject existing reparse points in a trusted path (not OS isolation)."""
    for parent in (path, *path.parents):
        if _is_symlink_or_reparse(parent):
            raise ArtifactPreparationError("ARTIFACT_PATH_REPARSE_DENIED")


def _trusted_https_url(url: str, *, release: bool = False) -> bool:
    if not isinstance(url, str) or not 1 <= len(url) <= 2048:
        return False
    if any(ord(c) < 33 or ord(c) >= 127 or c == "\\" for c in url):
        return False
    try:
        p = urlsplit(url)
        if p.scheme != "https" or p.hostname is None or p.username is not None or p.password is not None:
            return False
        if p.port not in (None, 443) or p.fragment:
            return False
        if release:
            return p.hostname == "github.com" and p.path.startswith(_GITHUB_REPO) and not p.query
        return p.hostname == "github.com" or p.hostname in _CDN_HOSTS
    except (ValueError, TypeError):
        return False


@dataclass(frozen=True)
class ArtifactSpec:
    """Pinned release metadata supplied by approved IWXA catalog, not the web.

    The manifest must be reviewed, versioned and held in an authoritative Agent
    catalog before this code runs. The downloaded archive is never allowed to
    supply its own expected hashes.
    """

    release_id: str
    platform: str
    archive_sha256: str
    executable_sha256: str
    download_url: str
    archive_format: str
    archive_name: str
    source_commit: str
    origin_guard_patch_sha256: str
    guard_protocol: str
    schema: str = SCHEMA

    @classmethod
    def from_mapping(cls, raw: dict) -> "ArtifactSpec":
        if not isinstance(raw, dict) or set(raw) != set(cls.__dataclass_fields__):
            raise ArtifactPreparationError("ARTIFACT_SPEC_FIELDS_INVALID")
        try:
            return cls(**raw)
        except (ValueError, TypeError) as exc:
            raise ArtifactPreparationError("ARTIFACT_SPEC_INVALID") from exc

    def __post_init__(self):
        if self.schema != SCHEMA or self.platform not in PLATFORMS:
            raise ValueError("unsupported artifact schema or platform")
        if self.source_commit != UPSTREAM_COMMIT or self.guard_protocol != GUARD_PROTOCOL:
            raise ValueError("unqualified origin-guard source / protocol")
        if not isinstance(self.origin_guard_patch_sha256, str) or _SHA.fullmatch(self.origin_guard_patch_sha256) is None:
            raise ValueError("pinned origin-guard patch SHA-256 required")
        if not isinstance(self.release_id, str) or _TOKEN.fullmatch(self.release_id) is None:
            raise ValueError("invalid release ID")
        if self.release_id in (".", "..") or self.release_id.startswith("."):
            raise ValueError("invalid release ID")
        if not all(isinstance(d, str) and _SHA.fullmatch(d) is not None
                   for d in (self.archive_sha256, self.executable_sha256)):
            raise ValueError("SHA-256 archive and executable pins required")
        if self.archive_format not in ("zip", "tar.gz"):
            raise ValueError("unsupported archive format")
        if self.platform.startswith("windows") and self.archive_format != "zip":
            raise ValueError("Windows package must be ZIP")
        if self.platform.startswith(("linux", "macos")) and self.archive_format != "tar.gz":
            raise ValueError("Linux package must be tar.gz")
        if not isinstance(self.archive_name, str) or _TOKEN.fullmatch(self.archive_name) is None:
            raise ValueError("invalid package filename")
        if not self.archive_name.endswith(".zip" if self.archive_format == "zip" else ".tar.gz"):
            raise ValueError("extension mismatch")
        if not _trusted_https_url(self.download_url, release=True):
            raise ValueError("untrusted release URL")
        expected_path = _GITHUB_REPO + self.release_id + "/" + self.archive_name
        if urlsplit(self.download_url).path != expected_path:
            raise ValueError("release URL not exactly bound to artifact identity")

    def as_dict(self) -> dict:
        return {field: getattr(self, field) for field in self.__dataclass_fields__}

    @property
    def fingerprint(self) -> str:
        b = json.dumps(self.as_dict(), ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(b).hexdigest()

    @property
    def executable_name(self) -> str:
        return PLATFORMS[self.platform]


@dataclass(frozen=True)
class PreparedArtifact:
    executable: str
    executable_sha256: str
    release_id: str
    platform: str
    artifact_fingerprint: str
    status: str = "STAGED_NOT_ACTIVATED"

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _sha_file(path: Path, limit: int) -> str:
    _check_parent_chain(path)
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
            raise ArtifactPreparationError("ARTIFACT_INPUT_NOT_REGULAR")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while True:
                chunk = stream.read(_CHUNK)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        raise ArtifactPreparationError("ARTIFACT_INPUT_UNREADABLE") from None


def _extract_one_binary(archive_path: Path, target: Path, spec: ArtifactSpec) -> None:
    """Do not use extractall: accept precisely one regular executable."""
    count = 0
    if spec.archive_format == "zip":
        try:
            with zipfile.ZipFile(archive_path) as archive:
                infos = archive.infolist()
                if len(infos) != 1:
                    raise ArtifactPreparationError("ARTIFACT_ARCHIVE_CONTENTS_INVALID")
                info = infos[0]
                if (info.filename != spec.executable_name or info.is_dir() or
                    stat.S_ISLNK(info.external_attr >> 16) or
                    not 0 < info.file_size <= BINARY_LIMIT):
                    raise ArtifactPreparationError("ARTIFACT_ARCHIVE_CONTENTS_INVALID")
                with archive.open(info) as source:
                    _write_bounded(source, target, BINARY_LIMIT)
                count += 1
        except (zipfile.BadZipFile, EOFError, OSError):
            raise ArtifactPreparationError("ARTIFACT_ARCHIVE_INVALID") from None
    else:
        try:
            with tarfile.open(archive_path, mode="r:gz") as archive:
                info = archive.next()
                if (info is None or info.name != spec.executable_name or not info.isfile() or
                    not 0 < info.size <= BINARY_LIMIT):
                    raise ArtifactPreparationError("ARTIFACT_ARCHIVE_CONTENTS_INVALID")
                body = archive.extractfile(info)
                if body is None:
                    raise ArtifactPreparationError("ARTIFACT_ARCHIVE_INVALID")
                with body:
                    _write_bounded(body, target, BINARY_LIMIT)
                if archive.next() is not None:
                    raise ArtifactPreparationError("ARTIFACT_ARCHIVE_CONTENTS_INVALID")
                count += 1
        except (tarfile.TarError, EOFError, OSError):
            raise ArtifactPreparationError("ARTIFACT_ARCHIVE_INVALID") from None
    if count != 1:
        raise ArtifactPreparationError("ARTIFACT_ARCHIVE_CONTENTS_INVALID")


def _write_bounded(source, target: Path, limit: int) -> None:
    amount = 0
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "wb") as writer:
        while True:
            chunk = source.read(_CHUNK)
            if not chunk:
                break
            amount += len(chunk)
            if amount > limit:
                raise ArtifactPreparationError("ARTIFACT_EXECUTABLE_TOO_LARGE")
            writer.write(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    if amount == 0:
        raise ArtifactPreparationError("ARTIFACT_EXECUTABLE_EMPTY")


class _SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _trusted_https_url(newurl) or urlsplit(newurl).hostname not in _CDN_HOSTS:
            raise ArtifactPreparationError("ARTIFACT_DOWNLOAD_REDIRECT_DENIED")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class ObscuraArtifactPreparer:
    """Pure preparation surface; no Agent/Core/ToolRuntime/PC3 dependency.

    Prepared directory is NOT an activated component, not a receipt and not
    an installation success claim. Agent is the authority for lifecycle.
    """

    def __init__(self, *, managed_root: str | Path):
        root = Path(managed_root)
        if not root.is_absolute() or str(root) in ("/", "\\"):
            raise ValueError("absolute managed Software Root required")
        _check_parent_chain(root)
        if root.exists() and not root.is_dir():
            raise ArtifactPreparationError("ARTIFACT_ROOT_INVALID")
        root.mkdir(parents=True, exist_ok=True)
        self.root = root

    def _target(self, spec: ArtifactSpec) -> Path:
        return self.root / "obscura-worker" / spec.release_id / spec.platform

    def verify_prepared(self, spec: ArtifactSpec) -> PreparedArtifact:
        target = self._target(spec)
        exe = target / spec.executable_name
        marker = target / "artifact-manifest.json"
        _check_parent_chain(target)
        if _is_symlink_or_reparse(exe) or _is_symlink_or_reparse(marker):
            raise ArtifactPreparationError("ARTIFACT_PATH_REPARSE_DENIED")
        try:
            if not target.is_dir() or not exe.is_file() or not marker.is_file():
                raise ArtifactPreparationError("ARTIFACT_PREPARED_MISSING")
            if marker.stat().st_size > 8192:
                raise ArtifactPreparationError("ARTIFACT_PREPARED_MARKER_INVALID")
            actual = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            raise ArtifactPreparationError("ARTIFACT_PREPARED_MARKER_INVALID") from None
        if (not isinstance(actual, dict) or set(actual) != {"schema", "spec_fingerprint"} or
                actual.get("schema") != SCHEMA or actual.get("spec_fingerprint") != spec.fingerprint):
            raise ArtifactPreparationError("ARTIFACT_PREPARED_MARKER_INVALID")
        if _sha_file(exe, BINARY_LIMIT) != spec.executable_sha256:
            raise ArtifactPreparationError("ARTIFACT_PREPARED_DIGEST_MISMATCH")
        return PreparedArtifact(executable=str(exe), executable_sha256=spec.executable_sha256,
                                release_id=spec.release_id, platform=spec.platform,
                                artifact_fingerprint=spec.fingerprint)

    def prepare_local(self, archive_path: str | Path, spec: ArtifactSpec) -> PreparedArtifact:
        archive_path = Path(archive_path)
        if not isinstance(spec, ArtifactSpec):
            raise ValueError("trusted ArtifactSpec required")
        if _sha_file(archive_path, ARCHIVE_LIMIT) != spec.archive_sha256:
            raise ArtifactPreparationError("ARTIFACT_ARCHIVE_DIGEST_MISMATCH")
        target = self._target(spec)
        parent = target.parent
        _check_parent_chain(parent)
        parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            return self.verify_prepared(spec)
        temp_dir = Path(tempfile.mkdtemp(prefix=".obscura-staging-", dir=parent))
        try:
            binary = temp_dir / spec.executable_name
            _extract_one_binary(archive_path, binary, spec)
            if _sha_file(binary, BINARY_LIMIT) != spec.executable_sha256:
                raise ArtifactPreparationError("ARTIFACT_EXECUTABLE_DIGEST_MISMATCH")
            if os.name != "nt":
                os.chmod(binary, 0o500)
            with (temp_dir / "artifact-manifest.json").open("x", encoding="utf-8") as sink:
                json.dump({"schema": SCHEMA, "spec_fingerprint": spec.fingerprint}, sink,
                          sort_keys=True, separators=(",", ":"))
                sink.flush()
                os.fsync(sink.fileno())
            try:
                os.rename(temp_dir, target)
            except FileExistsError:
                return self.verify_prepared(spec)
            except OSError:
                raise ArtifactPreparationError("ARTIFACT_STAGE_PUBLISH_FAILED") from None
            return self.verify_prepared(spec)
        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir)

    def download_and_prepare(
        self, spec: ArtifactSpec, *, timeout_seconds: float = 30,
        on_progress: Callable[[int, int | None], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> PreparedArtifact:
        """Download a pinned release on explicit request; never auto-update.

        The host may show progress and cancel its own request. The transient
        archive is always cleaned on failure/cancel. Use the OS HTTPS proxy
        configuration where present, still validating TLS, redirects and SHA.
        """
        if not isinstance(spec, ArtifactSpec):
            raise ValueError("trusted ArtifactSpec required")
        if not isinstance(timeout_seconds, (int, float)) or not 1 <= timeout_seconds <= 60:
            raise ValueError("invalid download timeout")
        if on_progress is not None and not callable(on_progress):
            raise ValueError("on_progress must be callable")
        if is_cancelled is not None and not callable(is_cancelled):
            raise ValueError("is_cancelled must be callable")
        if is_cancelled is not None and is_cancelled():
            raise ArtifactPreparationError("ARTIFACT_DOWNLOAD_CANCELLED")
        _check_parent_chain(self.root)
        # Honor system and corporate HTTPS proxy settings. The worker itself
        # still gets its separately isolated environment.
        opener = build_opener(ProxyHandler(), HTTPSHandler(context=ssl.create_default_context()),
                              _SafeRedirect())
        with tempfile.TemporaryDirectory(prefix=".obscura-download-", dir=self.root) as workdir:
            local = Path(workdir) / spec.archive_name
            try:
                with opener.open(Request(spec.download_url, headers={"User-Agent": "IWXA-Obscura-Preparer/1.0"}),
                                 timeout=timeout_seconds) as response:
                    raw_length = getattr(response, "headers", {}).get("Content-Length")
                    total = None
                    if raw_length is not None:
                        try:
                            value = int(raw_length)
                            if value >= 0:
                                total = value
                        except (ValueError, TypeError):
                            pass
                    if total is not None and total > ARCHIVE_LIMIT:
                        raise ArtifactPreparationError("ARTIFACT_DOWNLOAD_TOO_LARGE")
                    if on_progress is not None:
                        on_progress(0, total)
                    with local.open("xb") as sink:
                        amount = 0
                        digest = hashlib.sha256()
                        while True:
                            if is_cancelled is not None and is_cancelled():
                                raise ArtifactPreparationError("ARTIFACT_DOWNLOAD_CANCELLED")
                            chunk = response.read(_CHUNK)
                            if not chunk:
                                break
                            amount += len(chunk)
                            if amount > ARCHIVE_LIMIT:
                                raise ArtifactPreparationError("ARTIFACT_DOWNLOAD_TOO_LARGE")
                            sink.write(chunk)
                            digest.update(chunk)
                            if on_progress is not None:
                                on_progress(amount, total)
                        sink.flush()
                        os.fsync(sink.fileno())
                    if digest.hexdigest() != spec.archive_sha256:
                        raise ArtifactPreparationError("ARTIFACT_ARCHIVE_DIGEST_MISMATCH")
            except ArtifactPreparationError:
                raise
            except HTTPError as exc:
                if exc.code == 404:
                    code = "ARTIFACT_DOWNLOAD_NOT_FOUND"
                elif exc.code in (401, 403):
                    code = "ARTIFACT_DOWNLOAD_ACCESS_DENIED"
                else:
                    code = "ARTIFACT_DOWNLOAD_HTTP_FAILED"
                raise ArtifactPreparationError(code) from exc
            except (TimeoutError, URLError) as exc:
                reason = getattr(exc, "reason", exc)
                if isinstance(reason, ssl.SSLError):
                    code = "ARTIFACT_DOWNLOAD_TLS_FAILED"
                elif isinstance(reason, TimeoutError):
                    code = "ARTIFACT_DOWNLOAD_TIMEOUT"
                else:
                    code = "ARTIFACT_DOWNLOAD_FAILED"
                raise ArtifactPreparationError(code) from exc
            except ssl.SSLError as exc:
                raise ArtifactPreparationError("ARTIFACT_DOWNLOAD_TLS_FAILED") from exc
            except (OSError, ValueError) as exc:
                raise ArtifactPreparationError("ARTIFACT_DOWNLOAD_FAILED") from exc
            return self.prepare_local(local, spec)
