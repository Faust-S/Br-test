"""No-network tests for worker greeting (portable Python subprocess only)."""
from __future__ import annotations

import io
from pathlib import Path
import subprocess
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from iwxa_obscura.handshake import (
    GUARD_GREETING, require_worker_greeting, WorkerNotQualified,
)


class GreetingTests(unittest.TestCase):
    def test_valid_greeting_without_consuming_next_line(self):
        stream = io.BytesIO(GUARD_GREETING + b'{"ok":true}\n')
        require_worker_greeting(stream, deadline=time.monotonic() + 1)
        self.assertEqual(stream.read(), b'{"ok":true}\n')

    def test_invalid_greeting_rejected(self):
        with self.assertRaisesRegex(WorkerNotQualified, "WORKER_READY_INVALID"):
            require_worker_greeting(io.BytesIO(b"OLD_MARKER\n"),
                                    deadline=time.monotonic() + 1)

    def test_missing_greeting_rejected(self):
        with self.assertRaisesRegex(WorkerNotQualified, "WORKER_READY_INVALID"):
            require_worker_greeting(io.BytesIO(b""), deadline=time.monotonic() + 1)

    def test_oversized_greeting_rejected(self):
        with self.assertRaisesRegex(WorkerNotQualified, "WORKER_READY_INVALID"):
            require_worker_greeting(io.BytesIO(b"X" * 1_000_000),
                                    deadline=time.monotonic() + 1)

    def test_stalled_process_never_gets_navigation(self):
        proc = subprocess.Popen(
            [sys.executable, "-u", "-c", "import sys;sys.stdin.readline()"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            with self.assertRaisesRegex(WorkerNotQualified, "WORKER_READY_TIMEOUT"):
                require_worker_greeting(proc.stdout,
                                        deadline=time.monotonic() + 0.1)
        finally:
            proc.kill()
            proc.wait(timeout=2)
            proc.stdin.close()
            proc.stdout.close()

    def test_real_process_greets_before_command(self):
        program = (
            "import sys;"
            "sys.stdout.buffer.write(b'IWXA_ORIGIN_NET_GUARD_V2_READY_NO_RENDER\\n');"
            "sys.stdout.buffer.flush();"
            "line=sys.stdin.readline();"
            "print('OK:'+line.strip(),flush=True)"
        )
        proc = subprocess.Popen([sys.executable, "-u", "-c", program],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
        try:
            require_worker_greeting(proc.stdout, deadline=time.monotonic() + 5)
            proc.stdin.write(b"navigate\n")
            proc.stdin.flush()
            self.assertEqual(proc.stdout.readline().rstrip(b"\r\n"), b"OK:navigate")
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=2)
            proc.stdin.close()
            proc.stdout.close()

    def test_invalid_limit_denied(self):
        with self.assertRaises(ValueError):
            require_worker_greeting(io.BytesIO(GUARD_GREETING),
                                    deadline=time.monotonic() + 1,max_bytes=2)


if __name__ == "__main__":
    unittest.main()
