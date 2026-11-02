"""Independent, no-GUI Obscura observation controller for an IWXA Agent adapter.

Only an explicitly approved HTTPS origin may be requested. This is a narrow
experiment, NOT an OS sandbox or a safe general-purpose web crawler.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from types import MappingProxyType
from .trust import PinnedCABundle, CertificateTrustError
from datetime import datetime, timezone
import hashlib
import ipaddress
import signal
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit, urlunsplit


class ObscuraControllerError(RuntimeError):
    """The code is safe to surface; no raw engine stderr, URL or page content."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


_ALLOWED_FORMATS = frozenset({"markdown", "text", "html", "links"})
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_RESERVED_SUFFIXES = (".localhost", ".local", ".internal", ".test", ".invalid", ".example")


def _public_host(host: str | None) -> str:
    if not host or not host.isascii() or len(host) > 253:
        raise ValueError("missing or non-ASCII hostname")
    host = host.lower()
    if host.endswith(_RESERVED_SUFFIXES) or host in {"localhost", "local"}:
        raise ValueError("reserved hostname")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("IP literals are not permitted")
    labels = host.split(".")
    if len(labels) < 2 or any(_LABEL.fullmatch(x) is None for x in labels):
        raise ValueError("invalid DNS hostname")
    if len(labels[-1]) < 2 or labels[-1].isdigit():
        raise ValueError("invalid top-level label")
    return host


def _url_parts(raw: str):
    if not isinstance(raw, str) or not raw or len(raw) > 4096:
        raise ValueError("invalid URL length")
    if any(ord(c) < 33 or ord(c) == 127 or c == "\\" for c in raw):
        raise ValueError("unsafe URL character")
    p = urlsplit(raw)
    if p.scheme.lower() != "https" or p.username is not None or p.password is not None:
        raise ValueError("HTTPS without credentials required")
    host = _public_host(p.hostname)
    if p.port not in (None, 443):
        raise ValueError("nonstandard port")
    return p, host


def _canonical_origin(raw: str) -> str:
    p, host = _url_parts(raw)
    if p.path or p.query or p.fragment or p.netloc.count("@"):
        raise ValueError("origin cannot have path or credentials")
    origin = "https://" + host
    if raw != origin:
        raise ValueError("origin must use canonical spelling")
    return origin


def approved_url(raw: str, origins: frozenset[str]) -> str:
    """Canonicalize requested URL and check its origin (not the redirect chain)."""
    try:
        p, host = _url_parts(raw)
        if "https://" + host not in origins:
            raise ValueError("unapproved origin")
        return urlunsplit(("https", host, p.path or "/", p.query, ""))
    except (ValueError, TypeError):
        raise ObscuraControllerError("ORIGIN_DENIED") from None


@dataclass(frozen=True)
class Observation:
    requested_url: str
    format: str
    content: str
    content_sha256: str
    content_bytes: int
    retrieved_at: str
    elapsed_ms: int
    certificate_trust_mode: str
    ca_bundle_sha256: str | None

    def as_data(self) -> dict:
        return {
            "requested_url": self.requested_url,
            "final_url": None,  # CLI fetch does not attest the post-redirect address.
            "http_status": None,  # A successful subprocess is not HTTP 200 evidence.
            "format": self.format,
            "content": self.content,
            "content_sha256": self.content_sha256,
            "content_bytes": self.content_bytes,
            "retrieved_at": self.retrieved_at,
            "elapsed_ms": self.elapsed_ms,
            "tls_trust": {
                "mode": self.certificate_trust_mode,
                "additional_ca_bundle_sha256": self.ca_bundle_sha256,
                "scope": "ENTIRE_CHILD_PROCESS_NOT_DOMAIN_RESTRICTED",
                "tls_chain_proof": "NOT_INDEPENDENTLY_ATTESTED",
            },
            "evidence_status": "SOURCE_CONTENT_CAPTURED_NOT_U1_VERIFIED",
            "origin_check": "REQUESTED_ORIGIN_ONLY",
        }


def _drain_limited(pipe, limit: int, sink: bytearray, overflow: threading.Event) -> None:
    try:
        while True:
            chunk = pipe.read(min(65536, limit + 1 - len(sink)))
            if not chunk:
                break
            sink.extend(chunk)
            if len(sink) > limit:
                overflow.set()
                break
    finally:
        pipe.close()


class ObscuraController:
    """One short-lived engine process per observation; no browser UI/profile."""

    def __init__(self, *, binary: str | Path, binary_sha256: str,
                 allowed_origins: frozenset[str], timeout_seconds: float = 20,
                 max_output_bytes: int = 512_000,
                 ca_bundles: Mapping[str, PinnedCABundle] | None = None):
        if not isinstance(binary_sha256, str) or not _SHA256.fullmatch(binary_sha256):
            raise ValueError("pinned SHA-256 required")
        if not isinstance(allowed_origins, frozenset) or not allowed_origins:
            raise ValueError("non-empty exact HTTPS origins required")
        try:
            if any(_canonical_origin(o) != o for o in allowed_origins):
                raise ValueError("non-canonical origin")
        except (ValueError, TypeError) as exc:
            raise ValueError("exact canonical public HTTPS origins required") from exc
        if not 1 <= timeout_seconds <= 60 or not 1024 <= max_output_bytes <= 2_000_000:
            raise ValueError("unsafe controller limits")
        self.binary = Path(binary).resolve()
        self.binary_sha256 = binary_sha256
        self.allowed_origins = allowed_origins
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes
        # Only authorized application configuration may supply CA roots. Page
        # contents and ToolRuntime web.fetch arguments cannot change this map.
        if ca_bundles is None:
            ca_bundles = {}
        if not isinstance(ca_bundles, Mapping) or len(ca_bundles) > 32:
            raise ValueError("CA profiles must be a bounded mapping")
        for origin, profile in ca_bundles.items():
            if (not isinstance(origin, str) or origin not in allowed_origins or
                    _canonical_origin(origin) != origin or
                    not isinstance(profile, PinnedCABundle)):
                raise ValueError("CA profile must match approved canonical origin")
        self.ca_bundles = MappingProxyType(dict(ca_bundles))

    def _verify_binary(self) -> None:
        try:
            if not self.binary.is_file():
                raise ObscuraControllerError("ENGINE_MISSING")
            digest = hashlib.sha256()
            with self.binary.open("rb") as src:
                for chunk in iter(lambda: src.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != self.binary_sha256:
                raise ObscuraControllerError("ENGINE_DIGEST_MISMATCH")
        except OSError:
            raise ObscuraControllerError("ENGINE_UNREADABLE") from None

    def observe(self, url: str, *, format: str = "markdown") -> Observation:
        if not isinstance(format, str) or format not in _ALLOWED_FORMATS:
            raise ObscuraControllerError("FORMAT_DENIED")
        safe_url = approved_url(url, self.allowed_origins)
        selected_origin = "https://" + urlsplit(safe_url).hostname
        selected_ca = self.ca_bundles.get(selected_origin)
        started = time.monotonic()
        self._verify_binary()
        # Non-privileged environment: never inherit OBSCURA_* settings,
        # credentials, proxy configuration, CDP token or user browser profile.
        preserve = ("SystemRoot", "WINDIR", "PATH", "TEMP", "TMP", "LANG", "TZ")
        env = {key: os.environ[key] for key in preserve if key in os.environ}
        env["NO_COLOR"] = "1"
        with tempfile.TemporaryDirectory(prefix="iwxa-obscura-") as workdir:
            # Temporary HOME/USERPROFILE prevents accidental reuse of a real
            # browser profile. Not a security sandbox for hostile web content.
            env["HOME"] = workdir
            env["USERPROFILE"] = workdir
            env["TEMP"] = env["TMP"] = env["TMPDIR"] = workdir
            trust_mode = "ENGINE_DEFAULT"
            ca_sha = None
            if selected_ca is not None:
                # SSL_CERT_FILE is supported by Obscura 0.2.6 in reqwest.  This
                # adds roots to the standard client; no --stealth is requested.
                # All child destinations (including redirects / JS requests)
                # share this root. This is NOT per-host TLS scoping.
                try:
                    cert_file = selected_ca.stage(workdir)
                except CertificateTrustError as exc:
                    raise ObscuraControllerError(exc.code) from None
                env["SSL_CERT_FILE"] = str(cert_file)
                trust_mode = "PINNED_ADDITIONAL_CA"
                ca_sha = selected_ca.sha256
            args = [str(self.binary), "fetch", safe_url, "--dump", format,
                    "--timeout", str(max(1, int(self.timeout_seconds))),
                    "--obey-robots", "--quiet"]
            try:
                launch = {}
                if os.name == "nt":
                    launch["creationflags"] = subprocess.CREATE_NO_WINDOW
                else:
                    launch["start_new_session"] = True
                p = subprocess.Popen(args, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                     cwd=workdir, env=env, close_fds=True, **launch)
            except OSError:
                raise ObscuraControllerError("ENGINE_LAUNCH_FAILED") from None
            out, err = bytearray(), bytearray()
            overflow = threading.Event()
            threads = [
                threading.Thread(target=_drain_limited,
                                 args=(p.stdout, self.max_output_bytes, out, overflow), daemon=True),
                threading.Thread(target=_drain_limited,
                                 args=(p.stderr, 16_384, err, overflow), daemon=True),
            ]
            for t in threads:
                t.start()
            deadline = time.monotonic() + self.timeout_seconds
            timed_out = False
            try:
                while p.poll() is None and not overflow.is_set():
                    if time.monotonic() >= deadline:
                        timed_out = True
                        break
                    time.sleep(0.015)
            finally:
                if p.poll() is None and (timed_out or overflow.is_set()):
                    if os.name == "posix":
                        try:
                            os.killpg(p.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    else:
                        p.kill()
                p.wait()
                for t in threads:
                    t.join(timeout=2)
            if timed_out:
                raise ObscuraControllerError("ENGINE_TIMEOUT")
            if overflow.is_set() or len(out) > self.max_output_bytes:
                raise ObscuraControllerError("ENGINE_OUTPUT_LIMIT")
            if p.returncode != 0:
                raise ObscuraControllerError("ENGINE_FETCH_FAILED")
        if not out:
            raise ObscuraControllerError("ENGINE_EMPTY_OUTPUT")
        data = bytes(out)
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise ObscuraControllerError("ENGINE_NON_UTF8_OUTPUT") from None
        return Observation(
            requested_url=safe_url, format=format,
            content=text,
            content_sha256=hashlib.sha256(data).hexdigest(),
            content_bytes=len(data),
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            elapsed_ms=max(0, round((time.monotonic() - started) * 1000)),
            certificate_trust_mode=trust_mode,
            ca_bundle_sha256=ca_sha,
        )
