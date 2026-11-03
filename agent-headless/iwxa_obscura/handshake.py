"""Bounded worker greeting: navigation is blocked until a verified marker."""
from __future__ import annotations
import threading
import time

GUARD_GREETING = b"IWXA_ORIGIN_NET_GUARD_V2_READY_NO_RENDER\n"


class WorkerNotQualified(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def require_worker_greeting(stream, *, deadline: float, max_bytes: int = 96) -> None:
    """Read a bounded line BEFORE sending navigation (POSIX and Windows pipes).

    The caller must terminate and reap the process if verification fails.
    """
    if not 48 <= max_bytes <= 512:
        raise ValueError("invalid greeting limit")
    result: list[bytes] = []
    ready = threading.Event()

    def read_once() -> None:
        try:
            result.append(stream.readline(max_bytes))
        except OSError:
            result.append(b"")
        finally:
            ready.set()

    threading.Thread(target=read_once, daemon=True).start()
    while not ready.wait(min(0.02, max(0.0, deadline - time.monotonic()))):
        if time.monotonic() >= deadline:
            raise WorkerNotQualified("WORKER_READY_TIMEOUT")
    if not result or result[0] != GUARD_GREETING:
        raise WorkerNotQualified("WORKER_READY_INVALID")
