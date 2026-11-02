"""Independent, no-GUI Obscura observation controller for an IWXA Agent adapter.

Only an explicitly approved HTTPS origin may be requested. This is a narrow
experiment, NOT an OS sandbox or a safe general-purpose web crawler.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import ipaddress
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


def approved_url(raw: str, origins: frozenset[str]) -> str:
    if not isinstance(raw, str) or not raw or len(raw) > 4096:
        raise ObscuraControllerError("INVALID_URL")
    if any(ord(c) < 33 or ord(c) == 127 or c == "\\" for c in raw):
        raise ObscuraControllerError("INVALID_URL")
    try:
        p = urlsplit(raw)
        host = p.hostname
        port = p.port
        if p.scheme.lower() != "https" or not host or p.username is not None or p.password is not None:
            raise ValueError("not a public https URL")
        if host.endswith((".localhost", ".local", ".internal", ".test")) or host in {"localhost", "local"}:
            raise ValueError("local host")
        ip = None
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            pass
        if ip is not None and not ip.is_global:
            raise ValueError("nonpublic address")
        # Explicit origins are exact, no subdomain wildcards. Ports must match.
        authority = host.lower() if ":" not in host else f"[{host.lower()}]"
        if port is not None:
            authority += f":{port}"
        origin = "https://" + authority
        if origin not in origins:
            raise ValueError("unapproved origin")
        return urlunsplit(("https", p.netloc, p.path or "/", p.query, ""))
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
                 max_output_bytes: int = 512_000):
        if not isinstance(binary_sha256, str) or not _SHA256.fullmatch(binary_sha256):
            raise ValueError("pinned SHA-256 required")
        if not isinstance(allowed_origins, frozenset) or not allowed_origins or any(
            not isinstance(o, str) or not o.startswith("https://") or o != o.lower() or
            "/" in o[len("https://"):] for o in allowed_origins
        ):
            raise ValueError("exact lowercase HTTPS origins required")
        if not 1 <= timeout_seconds <= 60 or not 1024 <= max_output_bytes <= 2_000_000:
            raise ValueError("unsafe controller limits")
        self.binary = Path(binary).resolve()
        self.binary_sha256 = binary_sha256
        self.allowed_origins = allowed_origins
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes

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
        if format not in _ALLOWED_FORMATS:
            raise ObscuraControllerError("FORMAT_DENIED")
        safe_url = approved_url(url, self.allowed_origins)
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
            args = [str(self.binary), "fetch", safe_url, "--dump", format,
                    "--timeout", str(max(1, int(self.timeout_seconds))),
                    "--obey-robots", "--quiet"]
            try:
                p = subprocess.Popen(args, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                     cwd=workdir, env=env)
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
        return Observation(
            requested_url=safe_url, format=format,
            content=data.decode("utf-8", errors="replace"),
            content_sha256=hashlib.sha256(data).hexdigest(),
            content_bytes=len(data),
            retrieved_at=datetime.now(timezone.utc).isoformat(),
        )
