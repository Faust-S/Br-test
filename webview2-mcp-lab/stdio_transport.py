"""Small persistent MCP stdio transport. No browser logic or tool authorization.

The trusted host supplies the executable and arguments. No automatic replay.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time


class StdioTransportError(RuntimeError):
    def __init__(self, code, detail="", *, dispatched=False, write=False):
        self.code = code
        self.dispatched = dispatched
        self.external_effect_state = "UNKNOWN" if dispatched and write else "NONE"
        super().__init__(code + (": " + str(detail)[:400] if detail else ""))


class StdioMcpTransport:
    def __init__(self, argv, *, cwd=None, env=None, stderr=None,
                 max_message_bytes=8_000_000, max_request_bytes=1_000_000):
        if not argv or not all(isinstance(arg, str) and arg for arg in argv):
            raise ValueError("trusted argv required")
        if max_message_bytes < 1024 or max_request_bytes < 1024:
            raise ValueError("invalid MCP message limits")
        self.max_message_bytes = max_message_bytes
        self.max_request_bytes = max_request_bytes
        self.process = subprocess.Popen(list(argv), cwd=cwd, env=env, shell=False,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=stderr if stderr is not None else subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0)
        self._lock = threading.Lock()
        self._closing = threading.Event()
        self._messages = queue.Queue(maxsize=64)
        self._reader_error = None
        self._sequence = 0
        self.negotiated = None
        self.catalog_epoch = 0
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self):
        try:
            while not self._closing.is_set():
                line = self.process.stdout.readline(self.max_message_bytes + 1)
                if not line:
                    self._reader_error = "stdio_eof"
                    return
                if len(line) > self.max_message_bytes or not line.endswith(b"\n"):
                    self._reader_error = "stdio_message_too_large_or_unframed"
                    return
                value = json.loads(line.decode("utf-8"))
                if not isinstance(value, dict) or value.get("jsonrpc") != "2.0":
                    self._reader_error = "stdio_invalid_envelope"
                    return
                self._messages.put_nowait(value)
        except (ValueError, UnicodeError, OSError, queue.Full):
            self._reader_error = "stdio_invalid_or_overflow"

    def _abort(self):
        self._closing.set()
        if self.process.poll() is None:
            try:
                self.process.kill()
            except OSError:
                pass

    def _send(self, message, deadline, *, write=False, dispatched=False):
        try:
            data = (json.dumps(message, ensure_ascii=True, allow_nan=False,
                               separators=(",", ":")) + "\n").encode("ascii")
        except (TypeError, ValueError) as exc:
            raise StdioTransportError("stdio_invalid_request", dispatched=dispatched, write=write) from exc
        if len(data) > self.max_request_bytes:
            raise StdioTransportError("stdio_request_too_large", dispatched=dispatched, write=write)
        if self._closing.is_set() or self.process.poll() is not None:
            raise StdioTransportError("stdio_closed", dispatched=dispatched, write=write)
        if time.monotonic() >= deadline:
            raise StdioTransportError("stdio_timeout_before_send", dispatched=dispatched, write=write)
        done = threading.Event()
        failures = []
        def write_frame():
            try:
                self.process.stdin.write(data)
                self.process.stdin.flush()
            except (OSError, ValueError) as exc:
                failures.append(exc)
            finally:
                done.set()
        threading.Thread(target=write_frame, daemon=True).start()
        if not done.wait(max(0, deadline - time.monotonic())) or failures:
            self._abort()
            raise StdioTransportError("stdio_send_failed", dispatched=True, write=write)

    def _receive(self, deadline, *, write=False):
        while True:
            if self._closing.is_set():
                raise StdioTransportError("stdio_closed", dispatched=True, write=write)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._abort()
                raise StdioTransportError("stdio_timeout", dispatched=True, write=write)
            try:
                return self._messages.get(timeout=min(0.1, remaining))
            except queue.Empty:
                if self._reader_error:
                    self._abort()
                    raise StdioTransportError(self._reader_error, dispatched=True, write=write)

    def _request(self, method, params, deadline, *, write=False):
        self._sequence += 1
        request_id = self._sequence
        self._send({"jsonrpc":"2.0","id":request_id,"method":method,"params":params}, deadline, write=write)
        while True:
            message = self._receive(deadline, write=write)
            if "method" in message:
                if "id" in message:
                    response = {"jsonrpc":"2.0","id":message["id"]}
                    if message["method"] == "ping":
                        response["result"] = {}
                    else:
                        response["error"] = {"code":-32601,"message":"Client method not supported"}
                    self._send(response, deadline, write=write, dispatched=True)
                elif message["method"] == "notifications/tools/list_changed":
                    self.catalog_epoch += 1
                continue
            if type(message.get("id")) is not int or message["id"] != request_id:
                self._abort()
                raise StdioTransportError("stdio_response_id_mismatch", dispatched=True, write=write)
            if ("error" in message) == ("result" in message):
                self._abort()
                raise StdioTransportError("stdio_invalid_response", dispatched=True, write=write)
            if "error" in message:
                err = message["error"]
                code = err.get("code", "invalid") if isinstance(err, dict) else "invalid"
                raise StdioTransportError("mcp_rpc_error", code, dispatched=True, write=write)
            if not isinstance(message["result"], dict):
                self._abort()
                raise StdioTransportError("stdio_invalid_result", dispatched=True, write=write)
            return message["result"]

    def request(self, method, params=None, *, timeout=70, write=False):
        deadline = time.monotonic() + timeout
        if not self._lock.acquire(timeout=max(0, timeout)):
            raise StdioTransportError("stdio_busy")
        try:
            if self.negotiated is None:
                raise StdioTransportError("stdio_not_initialized")
            return self._request(method, params or {}, deadline, write=write)
        finally:
            self._lock.release()

    def initialize(self, *, client_name, client_version, protocol_version="2025-11-25", timeout=70):
        with self._lock:
            if self.negotiated is not None:
                return self.negotiated
            deadline = time.monotonic() + timeout
            try:
                result = self._request("initialize", {"protocolVersion":protocol_version,
                    "capabilities":{},"clientInfo":{"name":client_name,"version":client_version}}, deadline)
                if (result.get("protocolVersion") != protocol_version or
                    not isinstance(result.get("serverInfo"), dict) or
                    not isinstance(result.get("capabilities"), dict)):
                    raise StdioTransportError("stdio_unsupported_negotiation", dispatched=True)
                self._send({"jsonrpc":"2.0","method":"notifications/initialized"}, deadline)
                self.negotiated = result
                return result
            except BaseException:
                self._abort()
                raise

    def close(self):
        self._closing.set()
        idle = self._lock.acquire(blocking=False)
        try:
            if idle:
                if self.process.stdin and not self.process.stdin.closed:
                    self.process.stdin.close()
            else:
                self._abort()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._abort()
                self.process.wait(timeout=5)
            self._reader.join(timeout=2)
            for pipe in (self.process.stdin, self.process.stdout):
                if pipe and not pipe.closed:
                    pipe.close()
        finally:
            if idle:
                self._lock.release()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
