"""Pinned, opt-in CA bundle staging for one short-lived Obscura process.

This module never changes the OS trust store, and never fetches certificates.
The caller must obtain administrative/user authorization independently. A
pinned CA is trusted by the ENTIRE child process, not just its initial URL.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import ssl
import stat

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_CERT_BLOCK = re.compile(
    rb"-----BEGIN CERTIFICATE-----\s*[A-Za-z0-9+/=\s]+?-----END CERTIFICATE-----",
    re.DOTALL,
)
_MAX_BUNDLE_BYTES = 256 * 1024
_MAX_CERTS = 16


class CertificateTrustError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class PinnedCABundle:
    """Configuration supplied by a trusted controller, not from webpage text.

    approval_ref is bookkeeping, not evidence that Agent actually approved it.
    The existing Agent permission owner must verify that separately.
    """

    path: str | Path
    sha256: str
    approval_ref: str

    def __post_init__(self):
        if not isinstance(self.sha256, str) or _SHA256.fullmatch(self.sha256) is None:
            raise ValueError("CA bundle requires a SHA-256 pin")
        if not isinstance(self.path, (str, Path)) or not str(self.path):
            raise ValueError("CA bundle path required")
        if not Path(self.path).is_absolute():
            raise ValueError("CA bundle path must be absolute")
        if (not isinstance(self.approval_ref, str) or
                not re.fullmatch(r"[A-Za-z0-9_.:-]{8,128}", self.approval_ref)):
            raise ValueError("external CA authorization reference required")

    def stage(self, workdir: str | Path) -> Path:
        """Validate pinned CA PEM, copy to a private temp file; no network."""
        source = Path(self.path)
        if source.is_symlink():
            raise CertificateTrustError("CA_SOURCE_SYMLINK_DENIED")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(source, flags)
        except OSError:
            raise CertificateTrustError("CA_SOURCE_UNAVAILABLE") from None
        try:
            mode = os.fstat(fd)
            if not stat.S_ISREG(mode.st_mode) or not 1 <= mode.st_size <= _MAX_BUNDLE_BYTES:
                raise CertificateTrustError("CA_SOURCE_INVALID")
            with os.fdopen(fd, "rb", closefd=False) as f:
                data = f.read(_MAX_BUNDLE_BYTES + 1)
        except OSError:
            raise CertificateTrustError("CA_SOURCE_UNREADABLE") from None
        finally:
            os.close(fd)
        if len(data) > _MAX_BUNDLE_BYTES:
            raise CertificateTrustError("CA_SOURCE_INVALID")
        if hashlib.sha256(data).hexdigest() != self.sha256:
            raise CertificateTrustError("CA_PIN_MISMATCH")
        blocks = _CERT_BLOCK.findall(data)
        if not 1 <= len(blocks) <= _MAX_CERTS:
            raise CertificateTrustError("CA_PEM_INVALID")
        if _CERT_BLOCK.sub(b"", data).strip():
            raise CertificateTrustError("CA_PEM_INVALID")
        try:
            pem = data.decode("ascii")
            ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).load_verify_locations(cadata=pem)
        except (UnicodeError, ssl.SSLError, ValueError):
            raise CertificateTrustError("CA_PEM_INVALID") from None
        target = Path(workdir) / "agent-pinned-ca.pem"
        try:
            fd_out = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                             getattr(os, "O_BINARY", 0), 0o600)
        except OSError:
            raise CertificateTrustError("CA_STAGE_FAILED") from None
        try:
            with os.fdopen(fd_out, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
        except OSError:
            raise CertificateTrustError("CA_STAGE_FAILED") from None
        return target
