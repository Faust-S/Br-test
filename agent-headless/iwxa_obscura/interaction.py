"""No-GUI interactive workflows through the existing Obscura JSON-line worker.

This is a strict conversation with the pinned, origin-gated worker, not a
browser engine, renderer, standalone Task Engine or new installation manager.
Each workflow uses one fresh worker and a fresh temporary profile.

Public operations never accept arbitrary JavaScript from the task. The only
evaluations are code templates for reading a DOM selector, following a real
anchor, or dispatching a reviewed DOM click with an observed postcondition.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from queue import Empty, Queue
import signal
import subprocess
import tempfile
import threading
import time
from typing import Callable
from urllib.parse import urlsplit

from .controller import ObscuraControllerError, _drain_limited, approved_url
from .handshake import WorkerNotQualified, require_worker_greeting
from .trust import CertificateTrustError
from .worker import ObscuraWorkerController, WorkerObservation

_FORMATS = frozenset({"text", "html"})


class ObscuraInteractionError(ObscuraControllerError):
    """Failure with explicit uncertainty about an already dispatched action."""

    def __init__(self, code: str, *, external_effect_state: str = "NONE"):
        super().__init__(code)
        self.external_effect_state = external_effect_state


def _selector(value: str) -> str:
    if (not isinstance(value, str) or not 1 <= len(value) <= 256
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)):
        raise ObscuraInteractionError("DOM_SELECTOR_DENIED")
    return value


def _format(value: str) -> str:
    if value not in _FORMATS:
        raise ObscuraInteractionError("FORMAT_DENIED")
    return value


class _Conversation:
    def __init__(self, proc, *, deadline, max_output_bytes, is_cancelled, overflow):
        self.proc = proc
        self.deadline = deadline
        self.limit = max_output_bytes
        self.is_cancelled = is_cancelled
        self.overflow = overflow
        self.queue: Queue[bytes | None] = Queue()
        self.bytes_read = 0
        self.reader = threading.Thread(target=self._pump, daemon=True)
        self.reader.start()

    def _pump(self):
        try:
            while True:
                line = self.proc.stdout.readline(self.limit + 1)
                if not line:
                    self.queue.put(None)
                    break
                self.bytes_read += len(line)
                if self.bytes_read > self.limit or not line.endswith(b"\n"):
                    self.overflow.set()
                    self.queue.put(None)
                    break
                self.queue.put(line)
        except OSError:
            self.queue.put(None)

    def _check(self):
        if self.overflow.is_set():
            raise ObscuraInteractionError("ENGINE_OUTPUT_LIMIT")
        try:
            if self.is_cancelled is not None and self.is_cancelled():
                raise ObscuraInteractionError("ENGINE_CANCELLED")
        except ObscuraInteractionError:
            raise
        except Exception:
            raise ObscuraInteractionError("ENGINE_CANCEL_CHECK_FAILED") from None
        if time.monotonic() >= self.deadline:
            raise ObscuraInteractionError("ENGINE_TIMEOUT")

    def request(self, command: dict):
        self._check()
        encoded = (json.dumps(command, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")
        if len(encoded) > 8192:
            raise ObscuraInteractionError("WORKER_COMMAND_TOO_LARGE")
        try:
            self.proc.stdin.write(encoded)
            self.proc.stdin.flush()
        except (OSError, ValueError, BrokenPipeError):
            raise ObscuraInteractionError("ENGINE_WORKER_FAILED") from None
        while True:
            self._check()
            try:
                line = self.queue.get(timeout=min(0.025, max(0.001, self.deadline - time.monotonic())))
            except Empty:
                continue
            if line is None:
                self._check()
                raise ObscuraInteractionError("ENGINE_WORKER_FAILED")
            try:
                response = json.loads(line.decode("utf-8"))
            except (UnicodeError, ValueError):
                raise ObscuraInteractionError("WORKER_PROTOCOL_INVALID") from None
            if not isinstance(response, dict) or response.get("ok") is not True or "result" not in response:
                raise ObscuraInteractionError("WORKER_COMMAND_REJECTED")
            return response["result"]

    def shutdown(self):
        if self.request({"cmd": "shutdown"}) != "bye":
            raise ObscuraInteractionError("WORKER_PROTOCOL_INVALID")
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=max(0.1, self.deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            raise ObscuraInteractionError("ENGINE_SHUTDOWN_TIMEOUT") from None
        if self.proc.returncode != 0:
            raise ObscuraInteractionError("ENGINE_WORKER_FAILED")


class ObscuraInteractiveController(ObscuraWorkerController):
    """Bounded multi-step DOM interaction through the unmodified worker API.

    Read/follow work is observational. A click may have an irreversible effect
    at the remote site; a DOM confirmation is NOT a server-side write receipt.
    """

    @contextmanager
    def _dialog(self, requested_url: str, *, is_cancelled: Callable[[], bool] | None):
        origin = "https://" + urlsplit(requested_url).hostname
        selected_ca = self.ca_bundles.get(origin)
        started = time.monotonic()
        deadline = started + self.timeout_seconds
        self._verify_binary()
        env = {k: os.environ[k] for k in ("SystemRoot", "WINDIR", "PATH", "LANG", "TZ") if k in os.environ}
        env.update({"NO_COLOR": "1", "OBSCURA_OBEY_ROBOTS": "1",
                    "IWXA_OBSCURA_ALLOWED_ORIGIN": origin})
        with tempfile.TemporaryDirectory(prefix="iwxa-obscura-dialog-") as workdir:
            env.update({"HOME": workdir, "USERPROFILE": workdir,
                        "TEMP": workdir, "TMP": workdir, "TMPDIR": workdir})
            trust_mode, ca_sha = "ENGINE_DEFAULT", None
            if selected_ca is not None:
                try:
                    pem = selected_ca.stage(workdir)
                except CertificateTrustError as exc:
                    raise ObscuraInteractionError(exc.code) from None
                env["SSL_CERT_FILE"] = str(pem)
                trust_mode, ca_sha = "PINNED_ADDITIONAL_CA", selected_ca.sha256
            launch = ({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt"
                      else {"start_new_session": True})
            try:
                proc = subprocess.Popen(
                    [str(self.binary)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, cwd=workdir, env=env, close_fds=True, **launch,
                )
            except OSError:
                raise ObscuraInteractionError("ENGINE_LAUNCH_FAILED") from None
            overflow = threading.Event()
            stderr = bytearray()
            stderr_reader = threading.Thread(
                target=_drain_limited, args=(proc.stderr, 16_384, stderr, overflow), daemon=True)
            stderr_reader.start()
            dialog = None
            try:
                try:
                    require_worker_greeting(proc.stdout, deadline=deadline)
                except WorkerNotQualified as exc:
                    raise ObscuraInteractionError(exc.code) from None
                dialog = _Conversation(
                    proc, deadline=deadline, max_output_bytes=self.max_output_bytes,
                    is_cancelled=is_cancelled, overflow=overflow,
                )
                yield dialog, trust_mode, ca_sha, started
            finally:
                if proc.poll() is None:
                    if os.name == "posix":
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    else:
                        # Windows Popen.kill() only terminates the parent PID.
                        # taskkill /T also reaps descendants of a hung worker.
                        try:
                            subprocess.run(
                                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=5,
                                creationflags=subprocess.CREATE_NO_WINDOW,
                                check=False,
                            )
                        except (OSError, subprocess.TimeoutExpired):
                            pass
                        if proc.poll() is None:
                            proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                if proc.stdin is not None and not proc.stdin.closed:
                    proc.stdin.close()
                for stream in (proc.stdout, proc.stderr):
                    if stream is not None and not stream.closed:
                        stream.close()
                stderr_reader.join(timeout=1)
                if dialog is not None:
                    dialog.reader.join(timeout=1)

    @staticmethod
    def _nav_info(raw, origin):
        if not isinstance(raw, dict):
            raise ObscuraInteractionError("WORKER_PROTOCOL_INVALID")
        url = raw.get("url")
        status = raw.get("status")
        title = raw.get("title")
        if (not isinstance(url, str) or type(status) is not int or
                not 100 <= status <= 599 or not isinstance(title, str) or len(title) > 8192):
            raise ObscuraInteractionError("WORKER_PROTOCOL_INVALID")
        try:
            final_url = approved_url(url, frozenset({origin}))
        except ObscuraControllerError:
            raise ObscuraInteractionError("WORKER_FINAL_ORIGIN_DENIED") from None
        return final_url, status, title

    def _observed(self, *, requested_url, info, content, format, origin,
                  trust_mode, ca_sha, started):
        if not isinstance(content, str):
            raise ObscuraInteractionError("WORKER_PROTOCOL_INVALID")
        data = content.encode("utf-8")
        if len(data) > self.max_output_bytes:
            raise ObscuraInteractionError("ENGINE_OUTPUT_LIMIT")
        final, status, title = self._nav_info(info, origin)
        return WorkerObservation(
            requested_url=requested_url, final_url=final,
            http_status=status, page_title=title, format=format, content=content,
            content_sha256=hashlib.sha256(data).hexdigest(), content_bytes=len(data),
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            elapsed_ms=max(0, round((time.monotonic() - started) * 1000)),
            certificate_trust_mode=trust_mode, ca_bundle_sha256=ca_sha,
            origin_guard_required=True,
        )

    def inspect(self, url: str, *, selector: str, is_cancelled=None) -> dict:
        """Read a real DOM element's text in a single worker process."""
        selector = _selector(selector)
        requested = approved_url(url, self.allowed_origins)
        origin = "https://" + urlsplit(requested).hostname
        script = ("(function(){const n=document.querySelector(" +
                  json.dumps(selector) + ");return n===null?null:String(n.textContent);})()")
        with self._dialog(requested, is_cancelled=is_cancelled) as (dialog, trust, ca, start):
            info = dialog.request({"cmd": "navigate", "url": requested, "waitUntil": self.wait_until})
            final, status, title = self._nav_info(info, origin)
            value = dialog.request({"cmd": "evaluate", "expression": script})
            if not isinstance(value, str):
                raise ObscuraInteractionError("DOM_SELECTOR_NOT_OBSERVED")
            dialog.shutdown()
        data = value.encode("utf-8")
        if len(data) > self.max_output_bytes:
            raise ObscuraInteractionError("ENGINE_OUTPUT_LIMIT")
        return {
            "requested_url": requested, "final_url": final, "http_status": status,
            "page_title": title, "selector": selector, "text": value,
            "text_sha256": hashlib.sha256(data).hexdigest(),
            "text_bytes": len(data), "elapsed_ms": max(0, round((time.monotonic() - start) * 1000)),
            "tls_trust": {"mode": trust, "additional_ca_bundle_sha256": ca},
            "evidence_status": "WORKER_DOM_TEXT_OBSERVED_NOT_U1_VERIFIED",
        }

    def list_links(self, url: str, *, max_links: int = 50, is_cancelled=None) -> dict:
        """Discover bounded same-origin links from the actual no-GUI DOM.

        This is read-only. The worker observes the page's first bounded set
        of anchors; the Python boundary rejects external/invalid URLs before
        returning link candidates to Agent. A link listing is not a crawl.
        """
        if type(max_links) is not int or not 1 <= max_links <= 100:
            raise ObscuraInteractionError("DOM_LINK_LIMIT_DENIED")
        requested = approved_url(url, self.allowed_origins)
        origin = "https://" + urlsplit(requested).hostname
        # A page can place external links before local navigation; read a
        # bounded surplus and filter in the trusted Python controller.
        candidate_limit = max_links * 8
        script = (
            "(function(){const n=document.querySelectorAll('a[href]');"
            "const out=[];const cap=" + str(candidate_limit) + ";"
            "for(let i=0;i<n.length&&out.length<cap;i++){"
            "const a=n[i];out.push({href:String(a.href),"
            "text:String(a.textContent||'').trim().slice(0,256)});}"
            "return out;})()"
        )
        with self._dialog(requested, is_cancelled=is_cancelled) as (dialog, trust, ca, start):
            info = dialog.request({"cmd": "navigate", "url": requested, "waitUntil": self.wait_until})
            final, status, title = self._nav_info(info, origin)
            raw = dialog.request({"cmd": "evaluate", "expression": script})
            if not isinstance(raw, list) or len(raw) > candidate_limit:
                raise ObscuraInteractionError("DOM_LINK_LIST_INVALID")
            dialog.shutdown()
        links = []
        seen = set()
        for index, item in enumerate(raw):
            if (not isinstance(item, dict) or not isinstance(item.get("href"), str)
                    or not isinstance(item.get("text"), str)
                    or len(item["href"]) > 4096 or len(item["text"]) > 256):
                continue
            try:
                target = approved_url(item["href"], frozenset({origin}))
            except ObscuraControllerError:
                continue
            if target in seen:
                continue
            seen.add(target)
            links.append({"url": target, "text": item["text"].strip(), "dom_index": index})
            if len(links) == max_links:
                break
        return {
            "requested_url": requested, "final_url": final, "http_status": status,
            "page_title": title, "links": links,
            "candidate_count": len(raw), "candidate_limit": candidate_limit,
            "result_limit": max_links,
            "possibly_truncated": len(raw) >= candidate_limit or len(links) >= max_links,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_ms": max(0, round((time.monotonic() - start) * 1000)),
            "tls_trust": {"mode": trust, "additional_ca_bundle_sha256": ca},
            "evidence_status": "WORKER_DOM_LINKS_OBSERVED_NOT_U1_VERIFIED",
            "external_effect_state": "NONE",
        }

    def follow_link(self, url: str, *, selector: str, format: str = "text",
                    is_cancelled=None) -> dict:
        """Follow an existing anchor on one approved origin, then read result."""
        selector, format = _selector(selector), _format(format)
        requested = approved_url(url, self.allowed_origins)
        origin = "https://" + urlsplit(requested).hostname
        script = ("(function(){const a=document.querySelector(" +
                  json.dumps(selector) +
                  ");return a&&a.tagName==='A'?{href:String(a.href),text:String(a.textContent)}:null;})()")
        with self._dialog(requested, is_cancelled=is_cancelled) as (dialog, trust, ca, start):
            first = dialog.request({"cmd": "navigate", "url": requested, "waitUntil": self.wait_until})
            first_url, first_status, _ = self._nav_info(first, origin)
            link = dialog.request({"cmd": "evaluate", "expression": script})
            if (not isinstance(link, dict) or not isinstance(link.get("href"), str)
                    or not isinstance(link.get("text"), str)
                    or len(link["text"]) > 8192):
                raise ObscuraInteractionError("DOM_LINK_NOT_OBSERVED")
            # Reject the target BEFORE sending a second navigation command.
            try:
                target = approved_url(link["href"], frozenset({origin}))
            except ObscuraControllerError as exc:
                raise ObscuraInteractionError(exc.code) from None
            second = dialog.request({"cmd": "navigate", "url": target, "waitUntil": self.wait_until})
            content = dialog.request({"cmd": "dump_html" if format == "html" else "dump_text"})
            dialog.shutdown()
        observation = self._observed(
            requested_url=requested, info=second, content=content, format=format,
            origin=origin, trust_mode=trust, ca_sha=ca, started=start,
        )
        return {**observation.as_data(),
                "operation": "FOLLOW_LINK", "source_final_url": first_url,
                "source_http_status": first_status, "selected_href": target,
                "selected_link_text": link["text"],
                "external_effect_state": "NONE"}

    def click_control(self, url: str, *, selector: str,
                      confirmation_selector: str, expected_text: str,
                      is_cancelled=None) -> dict:
        """Dispatch a DOM click; confirm only local DOM state, never remote write.

        Callers MUST require Agent's consequential-action plan/idempotency
        before invoking. The worker cannot provide server-side receipts.
        """
        selector = _selector(selector)
        confirmation_selector = _selector(confirmation_selector)
        if (not isinstance(expected_text, str) or not 1 <= len(expected_text) <= 512
                or any(ord(c) < 32 or ord(c) == 127 for c in expected_text)):
            raise ObscuraInteractionError("DOM_POSTCONDITION_DENIED")
        requested = approved_url(url, self.allowed_origins)
        origin = "https://" + urlsplit(requested).hostname
        click_js = ("(function(){const n=document.querySelector(" + json.dumps(selector) +
                    ");if(!n||typeof n.click!=='function')return {dispatched:false};"
                    "n.click();return {dispatched:true};})()")
        read_js = ("(function(){const n=document.querySelector(" +
                   json.dumps(confirmation_selector) +
                   ");return n===null?null:String(n.textContent);})()")
        attempted = False
        try:
            with self._dialog(requested, is_cancelled=is_cancelled) as (dialog, trust, ca, start):
                before = dialog.request({"cmd": "navigate", "url": requested, "waitUntil": self.wait_until})
                final, status, title = self._nav_info(before, origin)
                prior_text = dialog.request({"cmd": "evaluate", "expression": read_js})
                if isinstance(prior_text, str) and expected_text in prior_text:
                    raise ObscuraInteractionError("DOM_POSTCONDITION_ALREADY_PRESENT")
                attempted = True  # From here, even a lost response may hide an effect.
                response = dialog.request({"cmd": "evaluate", "expression": click_js})
                if not isinstance(response, dict) or response.get("dispatched") is not True:
                    attempted = False if response == {"dispatched": False} else True
                    raise ObscuraInteractionError("DOM_CLICK_NOT_CONFIRMED")
                # Client-side actions often update the DOM asynchronously.
                # Poll within a strict subdeadline; never retry the click.
                confirmation_deadline = min(dialog.deadline, time.monotonic() + 3)
                text = None
                while time.monotonic() < confirmation_deadline:
                    text = dialog.request({"cmd": "evaluate", "expression": read_js})
                    if isinstance(text, str) and expected_text in text:
                        break
                    time.sleep(0.05)
                else:
                    raise ObscuraInteractionError("DOM_POSTCONDITION_NOT_OBSERVED")
                dialog.shutdown()
        except ObscuraControllerError as exc:
            raise ObscuraInteractionError(
                exc.code, external_effect_state="UNKNOWN" if attempted else "NONE",
            ) from None
        raw = text.encode("utf-8")
        return {
            "requested_url": requested, "final_url": final, "http_status": status,
            "page_title": title, "selector": selector,
            "confirmation_selector": confirmation_selector,
            "expected_text": expected_text, "observed_text_sha256": hashlib.sha256(raw).hexdigest(),
            "dom_postcondition_observed": True,
            "elapsed_ms": max(0, round((time.monotonic() - start) * 1000)),
            "tls_trust": {"mode": trust, "additional_ca_bundle_sha256": ca},
            "evidence_status": "DOM_CLICK_DISPATCHED_LOCAL_POSTCONDITION_ONLY",
            "external_effect_state": "UNKNOWN",
        }
