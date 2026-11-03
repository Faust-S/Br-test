"""Obscura's separate no-GUI JSON-line worker as an IWXA observation provider.

This controller reports the worker-observed final document URL and HTTP status.
It does NOT isolate network egress, redirects or subresources at the OS level.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
import signal
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit

from .controller import ObscuraController, ObscuraControllerError, _drain_limited, approved_url
from .handshake import require_worker_greeting, WorkerNotQualified
from .trust import CertificateTrustError

_WAIT_UNTIL = frozenset({"load", "domcontentloaded", "networkidle0", "networkidle2"})


@dataclass(frozen=True)
class WorkerObservation:
    requested_url: str
    final_url: str
    http_status: int
    page_title: str
    format: str
    content: str
    content_sha256: str
    content_bytes: int
    retrieved_at: str
    elapsed_ms: int
    certificate_trust_mode: str
    ca_bundle_sha256: str | None
    origin_guard_required: bool

    def as_data(self) -> dict:
        return {
            "requested_url": self.requested_url,
            "final_url": self.final_url,
            "http_status": self.http_status,
            "page_title": self.page_title,
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
            "evidence_status": "WORKER_DOCUMENT_OBSERVED_NOT_U1_VERIFIED",
            "origin_check": "REQUESTED_AND_REPORTED_FINAL_ORIGIN_ONLY",
            "network_isolation": "IWXA_GUARD_V2_GREETING_CONFIRMED_NO_OS_SANDBOX",
        }


class ObscuraWorkerController(ObscuraController):
    """Uses a separately built 'obscura-worker' binary, NOT the 'obscura' CLI.

    The worker supports dump_text / dump_html, not markdown. Its navigation
    metadata is more useful than CLI fetch but is not a cryptographic attestation.
    """

    def __init__(self, *, wait_until: str = "networkidle2", **kwargs):
        super().__init__(**kwargs)
        if wait_until not in _WAIT_UNTIL:
            raise ValueError("unsupported worker navigation readiness")
        self.wait_until = wait_until

    def observe(self, url: str, *, format: str = "text") -> WorkerObservation:
        if format not in ("text", "html"):
            raise ObscuraControllerError("FORMAT_DENIED")
        requested_url = approved_url(url, self.allowed_origins)
        origin = "https://" + urlsplit(requested_url).hostname
        selected_ca = self.ca_bundles.get(origin)
        started = time.monotonic()
        self._verify_binary()
        env_names = ("SystemRoot", "WINDIR", "PATH", "LANG", "TZ")
        env = {k: os.environ[k] for k in env_names if k in os.environ}
        env["NO_COLOR"] = "1"
        env["OBSCURA_OBEY_ROBOTS"] = "1"
        # The patched obscura-net URL gate reads this before every transport
        # request, including redirects and JS/iframe requests. This is NOT an
        # operating-system network sandbox and remains build-specific.
        env["IWXA_OBSCURA_ALLOWED_ORIGIN"] = origin
        commands = (
            {"cmd": "navigate", "url": requested_url, "waitUntil": self.wait_until},
            {"cmd": "dump_text" if format == "text" else "dump_html"},
            {"cmd": "shutdown"},
        )
        payload = ("\n".join(json.dumps(c, ensure_ascii=True, separators=(",", ":"))
                             for c in commands) + "\n").encode("ascii")
        with tempfile.TemporaryDirectory(prefix="iwxa-obscura-worker-") as workdir:
            env.update({"HOME": workdir, "USERPROFILE": workdir,
                        "TEMP": workdir, "TMP": workdir, "TMPDIR": workdir})
            trust_mode = "ENGINE_DEFAULT"
            ca_sha = None
            if selected_ca is not None:
                try:
                    cert_file = selected_ca.stage(workdir)
                except CertificateTrustError as exc:
                    raise ObscuraControllerError(exc.code) from None
                env["SSL_CERT_FILE"] = str(cert_file)
                trust_mode = "PINNED_ADDITIONAL_CA"
                ca_sha = selected_ca.sha256
            launch = ({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt"
                      else {"start_new_session": True})
            try:
                proc = subprocess.Popen([str(self.binary)], cwd=workdir, env=env,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, close_fds=True, **launch)
            except OSError:
                raise ObscuraControllerError("ENGINE_LAUNCH_FAILED") from None
            out, err = bytearray(), bytearray()
            overflow = threading.Event()
            stderr_reader = threading.Thread(
                target=_drain_limited,
                args=(proc.stderr, 16_384, err, overflow), daemon=True)
            stderr_reader.start()
            readers = [stderr_reader]
            timed_out = False
            failure = None
            try:
                # Only our pinned, native-guarded binary emits this greeting.
                # Never send a navigation before it is read and verified.
                try:
                    require_worker_greeting(
                        proc.stdout, deadline=started + self.timeout_seconds)
                except WorkerNotQualified as exc:
                    failure = ObscuraControllerError(exc.code)
                if failure is None and not overflow.is_set():
                    stdout_reader = threading.Thread(
                        target=_drain_limited,
                        args=(proc.stdout, self.max_output_bytes, out, overflow),
                        daemon=True)
                    stdout_reader.start()
                    readers.append(stdout_reader)
                    try:
                        proc.stdin.write(payload)
                        proc.stdin.close()
                    except (OSError, BrokenPipeError):
                        failure = ObscuraControllerError("ENGINE_WORKER_FAILED")
                    if failure is None:
                        deadline = started + self.timeout_seconds
                        while proc.poll() is None and not overflow.is_set():
                            if time.monotonic() >= deadline:
                                timed_out = True
                                break
                            time.sleep(0.015)
            finally:
                if proc.poll() is None and (failure is not None or timed_out or overflow.is_set()):
                    if os.name == "posix":
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    else:
                        proc.kill()
                proc.wait()
                if proc.stdin is not None and not proc.stdin.closed:
                    proc.stdin.close()
                for reader in readers:
                    reader.join(timeout=2)
            if failure is not None:
                raise failure
            if timed_out:
                raise ObscuraControllerError("ENGINE_TIMEOUT")
            if overflow.is_set() or len(out) > self.max_output_bytes:
                raise ObscuraControllerError("ENGINE_OUTPUT_LIMIT")
            if proc.returncode:
                raise ObscuraControllerError("ENGINE_WORKER_FAILED")

        try:
            messages = [json.loads(line) for line in bytes(out).decode("utf-8").splitlines()]
            if len(messages) != 3 or any(not isinstance(v, dict) or v.get("ok") is not True
                                         for v in messages):
                raise ValueError("bad worker envelope")
            if messages[2].get("result") != "bye":
                raise ValueError("shutdown response missing")
            info = messages[0]["result"]
            if not isinstance(info, dict) or not isinstance(info.get("url"), str):
                raise ValueError("document URL missing")
            status = info.get("status")
            if type(status) is not int or not 100 <= status <= 599:
                raise ValueError("HTTP document status missing or invalid")
            title = info.get("title")
            if not isinstance(title, str) or len(title) > 8192:
                raise ValueError("invalid title")
            content = messages[1].get("result")
            if not isinstance(content, str):
                raise ValueError("invalid text payload")
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            raise ObscuraControllerError("WORKER_PROTOCOL_INVALID") from None
        try:
            final_url = approved_url(info["url"], frozenset({origin}))
        except ObscuraControllerError:
            # The navigation may already have reached another site. Rejecting
            # its result is NOT a network firewall; a separate sandbox is needed.
            raise ObscuraControllerError("WORKER_FINAL_ORIGIN_DENIED") from None
        content_bytes = content.encode("utf-8")
        if len(content_bytes) > self.max_output_bytes:
            raise ObscuraControllerError("ENGINE_OUTPUT_LIMIT")
        return WorkerObservation(
            requested_url=requested_url, final_url=final_url, http_status=status,
            page_title=title, format=format, content=content,
            content_sha256=hashlib.sha256(content_bytes).hexdigest(),
            content_bytes=len(content_bytes),
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            elapsed_ms=max(0, round((time.monotonic() - started) * 1000)),
            certificate_trust_mode=trust_mode, ca_bundle_sha256=ca_sha,
            origin_guard_required=True,
        )
