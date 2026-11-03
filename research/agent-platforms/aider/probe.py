"""Independent Aider 0.86.0 real Git-edit trial with local Qwen2.5-Coder 1.5b.

Tests actual Aider source editing via actual Ollama inference. A result is PASS
only if baseline tests fail, Aider makes a nonempty tracked source diff, no
tests are altered, and an independent post-edit unittest run passes.

No private IWXA sources, cloud model, API keys, or paid services are used.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time
import traceback
from urllib.request import urlopen

MODEL = "qwen2.5-coder:1.5b"
AIDER_VERSION = "0.86.0"
OUT = Path("artifacts/agent-platforms/aider-real-git-edit.json")

BUGGY_SOURCE = '''"""Invoice math in integer cents; shipping is never discounted."""


def invoice_due_cents(subtotal_cents: int, shipping_cents: int, discount_pct: int = 0) -> int:
    """Compute total due, with discounts on merchandise subtotal only."""
    if subtotal_cents < 0 or shipping_cents < 0:
        raise ValueError("Amounts must be non-negative")
    if not 0 <= discount_pct <= 100:
        raise ValueError("Discount must be within 0..100 percent")
    discount_cents = (subtotal_cents + shipping_cents) * discount_pct // 100
    return subtotal_cents + shipping_cents - discount_cents
'''

REGRESSION_TESTS = '''import unittest
from src.invoice import invoice_due_cents


class InvoiceRegression(unittest.TestCase):
    def test_no_discount(self):
        self.assertEqual(invoice_due_cents(2179, 320), 2499)

    def test_ten_percent_excludes_shipping(self):
        self.assertEqual(invoice_due_cents(1250, 320, 10), 1445)

    def test_percent_integer_cents_round_down(self):
        self.assertEqual(invoice_due_cents(399, 49, 17), 381)

    def test_full_discount_preserves_shipping(self):
        self.assertEqual(invoice_due_cents(450, 25, 100), 25)

    def test_shipping_only_preserved(self):
        self.assertEqual(invoice_due_cents(0, 35, 100), 35)

    def test_rejects_negative_inputs(self):
        with self.assertRaises(ValueError):
            invoice_due_cents(-1, 1)
        with self.assertRaises(ValueError):
            invoice_due_cents(1, -1)

    def test_rejects_invalid_percent(self):
        with self.assertRaises(ValueError):
            invoice_due_cents(10, 4, 101)
        with self.assertRaises(ValueError):
            invoice_due_cents(10, 4, -1)


if __name__ == "__main__":
    unittest.main()
'''

TASK = (
    "Fix the actual bug in src/invoice.py. invoice_due_cents incorrectly "
    "applies the percentage discount to shipping as well as to merchandise. "
    "Discount ONLY the merchandise subtotal, using integer cents with floor "
    "rounding. Shipping must never be discounted, including at 100 percent. "
    "Preserve function name, signature, type hints, input validation, and "
    "zero-discount behavior. Edit ONLY src/invoice.py, do not modify tests. "
    "Apply the source code change now; do not just explain the patch."
)


def run(args: list[str], *, cwd: Path, env: dict[str, str], timeout: int) -> dict:
    start = time.monotonic()
    try:
        process = subprocess.run(
            args, cwd=cwd, env=env, capture_output=True,
            text=True, timeout=timeout, check=False,
        )
        return {
            "returncode": process.returncode,
            "elapsed_seconds": round(time.monotonic() - start, 3),
            "stdout_tail": process.stdout[-5500:],
            "stderr_tail": process.stderr[-6500:],
        }
    except subprocess.TimeoutExpired as exc:
        return {"returncode": 124, "timeout_seconds": timeout,
                "elapsed_seconds": round(time.monotonic() - start, 3),
                "error": type(exc).__name__}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tags() -> list[dict]:
    with urlopen("http://127.0.0.1:11434/api/tags", timeout=10) as response:
        all_models = json.load(response).get("models", [])
    return [
        {"name": x.get("name"), "digest": x.get("digest"), "size": x.get("size")}
        for x in all_models if x.get("name") == MODEL
    ]


def exercise(report: dict) -> None:
    with tempfile.TemporaryDirectory(prefix="iwxa-public-aider-") as home:
        root = Path(home) / "invoice-demo"
        (root / "src").mkdir(parents=True)
        (root / "tests").mkdir()
        source_path = root / "src" / "invoice.py"
        test_path = root / "tests" / "test_invoice.py"
        (root / "src" / "__init__.py").write_text("", encoding="utf-8")
        source_path.write_text(BUGGY_SOURCE, encoding="utf-8")
        test_path.write_text(REGRESSION_TESTS, encoding="utf-8")

        environ = dict(os.environ)
        environ.update({
            "HOME": str(Path(home) / "home"),
            "XDG_CONFIG_HOME": str(Path(home) / "config"),
            "XDG_CACHE_HOME": str(Path(home) / "cache"),
            "OLLAMA_API_BASE": "http://127.0.0.1:11434",
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
            "AIDER_ANALYTICS_DISABLE": "true",
            "PYTHONUTF8": "1",
            "GIT_TERMINAL_PROMPT": "0",
        })
        for path in ("home", "config", "cache"):
            (Path(home) / path).mkdir()
        report["local_model"] = tags()
        if not report["local_model"]:
            raise RuntimeError("requested local model is not available")

        checks = [
            ["git", "init", "-q"],
            ["git", "config", "user.name", "IWXA Public Research"],
            ["git", "config", "user.email", "iwxa-research@example.invalid"],
            ["git", "add", "src/invoice.py", "src/__init__.py", "tests/test_invoice.py"],
            ["git", "commit", "-qm", "Fixture: failing invoice percent-discount case"],
        ]
        for args in checks:
            result = run(args, cwd=root, env=environ, timeout=15)
            if result["returncode"] != 0:
                raise RuntimeError("fixture git setup failed: " + str(result))

        before_source = sha(source_path)
        before_tests = sha(test_path)
        test_command = [sys.executable, "-m", "unittest", "discover",
                        "-s", "tests", "-p", "test_invoice.py", "-v"]
        baseline = run(test_command, cwd=root, env=environ, timeout=25)
        report["baseline"] = baseline
        if baseline["returncode"] == 0 or "FAILED" not in baseline["stderr_tail"]:
            raise AssertionError("baseline must demonstrate the original bug")

        model_version = run(["aider", "--version"], cwd=root, env=environ, timeout=20)
        report["aider_version"] = model_version
        if model_version["returncode"] != 0 or AIDER_VERSION not in (
                model_version.get("stdout_tail", "") + model_version.get("stderr_tail", "")):
            raise AssertionError("wrong Aider version installed")

        cmd = [
            "aider", "--model", "ollama_chat/" + MODEL,
            "--edit-format", "whole",
            "--no-auto-commits", "--no-auto-lint",
            "--no-stream", "--no-analytics", "--no-check-update",
            "--no-show-model-warnings", "--no-fancy-input",
            "--no-suggest-shell-commands",
            "--map-tokens", "0", "--yes-always",
            "--message", TASK, "src/invoice.py",
        ]
        report["aider_execution"] = run(cmd, cwd=root, env=environ, timeout=220)
        report["aider_source_modified"] = sha(source_path) != before_source
        report["regression_tests_unchanged"] = sha(test_path) == before_tests

        tracked_files = run(["git", "diff", "--name-only"], cwd=root, env=environ, timeout=12)
        report["modified_tracked_paths"] = [
            x for x in tracked_files.get("stdout_tail", "").splitlines() if x.strip()
        ]
        patch = run(["git", "diff", "--", "src/invoice.py"], cwd=root, env=environ, timeout=12)
        report["source_git_patch"] = patch.get("stdout_tail", "")[:5300]

        after = run(test_command, cwd=root, env=environ, timeout=30)
        report["post_edit_tests"] = after
        report["function_output_examples"] = {}
        if after["returncode"] == 0:
            import importlib.util
            spec = importlib.util.spec_from_file_location("invoice_trial", source_path)
            if spec is None or spec.loader is None:
                raise RuntimeError("edited module could not be imported")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            report["function_output_examples"] = {
                "1250_320_10": module.invoice_due_cents(1250, 320, 10),
                "399_49_17": module.invoice_due_cents(399, 49, 17),
                "450_25_100": module.invoice_due_cents(450, 25, 100),
            }
        report["status"] = "PASS" if (
            report["aider_execution"]["returncode"] == 0
            and report["aider_source_modified"]
            and report["regression_tests_unchanged"]
            and report["modified_tracked_paths"] == ["src/invoice.py"]
            and after["returncode"] == 0
            and all((report["function_output_examples"].get(key) == expected)
                    for key, expected in [("1250_320_10", 1445),
                                           ("399_49_17", 381),
                                           ("450_25_100", 25)])
        ) else "FAIL"


def main() -> int:
    report = {
        "program": "Aider", "version_expected": AIDER_VERSION,
        "model": MODEL, "runner": platform.platform(),
        "scope": "REAL_AIDER_CLI_LOCAL_MODEL_GIT_PATCH_AND_INDEPENDENT_TEST",
        "private_iwxa_adapter": "NOT_TESTED", "iwxa_task_result": "NOT_TESTED",
        "status": "FAIL",
    }
    try:
        exercise(report)
    except Exception as exc:
        report["error"] = type(exc).__name__ + ": " + str(exc)[:600]
        report["traceback_tail"] = traceback.format_exc()[-1800:]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("IWXA_AIDER_REAL_GIT_TRIAL", json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
