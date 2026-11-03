"""Actual Aider coding trial against a frozen real public Br-test source file.

Copies the existing screenshot comparison module and its test suite into a
disposable Git project; adds an independent nearest-rank p95 regression;
then runs real Aider 0.86.0 + local Ollama coder model. No production branch
file is edited, no private IWXA source is transferred.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from urllib.request import urlopen

REPO = Path(__file__).resolve().parents[3]
SOURCE = REPO / "browser-lab/benchmarks/compare_frames.py"
EXISTING_TEST = REPO / "browser-lab/benchmarks/test_compare_frames.py"
EXPECTED_SOURCE_BLOB = "8ea7a15e874b6ae5c169bdb4a31d4e6e16229c55"
OUT = Path("artifacts/agent-platforms/aider-real-source.json")
MODEL = "qwen2.5-coder:3b"
AIDER_VERSION = "0.86.0"
REGRESSION = '''"""Additional independent nearest-rank p95 cases for real comparison module."""
import unittest
from PIL import Image
from compare_frames import score

class P95BoundaryRegression(unittest.TestCase):
    def test_p95_nearest_rank_for_twenty_pixels(self):
        reference = Image.new("RGB", (20, 1), (0, 0, 0))
        candidate = reference.copy()
        candidate.putpixel((18, 0), (100, 0, 0))
        candidate.putpixel((19, 0), (200, 0, 0))
        # Sorted errors: eighteen 0s, 100, 200. Nearest rank 95% = item 19.
        self.assertEqual(score(reference, candidate)["max_channel_error_p95_0_to_255"], 100)

    def test_p95_nearest_rank_for_forty_pixels(self):
        reference = Image.new("RGB", (40, 1), (0, 0, 0))
        candidate = reference.copy()
        candidate.putpixel((37, 0), (80, 0, 0))
        candidate.putpixel((38, 0), (120, 0, 0))
        candidate.putpixel((39, 0), (220, 0, 0))
        # Rank ceil(0.95*40)=38, zero-based 37: value 80.
        self.assertEqual(score(reference, candidate)["max_channel_error_p95_0_to_255"], 80)

if __name__ == "__main__":
    unittest.main()
'''

TASK = (
    "Fix the percentile boundary bug in the actual Br-test screenshot-fidelity "
    "module compare_frames.py. The max_channel_error_p95_0_to_255 field must "
    "implement the nearest-rank 95th percentile: sort the per-pixel maximum "
    "channel errors and select the element with zero-based index "
    "ceil(0.95 * count) - 1, correctly clamped to available indices. "
    "At N=20 this must select index 18 rather than index 19. "
    "Preserve all other metrics, public function names, argument behavior, "
    "and provenance validation. Change ONLY compare_frames.py. "
    "Do not touch any tests. Apply the source fix, not merely explain it."
)


def sha256(file: Path) -> str:
    return hashlib.sha256(file.read_bytes()).hexdigest()


def cmd(args, root: Path, env, timeout=160):
    started = time.monotonic()
    try:
        p = subprocess.run(args, cwd=root, env=env, capture_output=True,
                           text=True, timeout=timeout, check=False)
        return {"exit": p.returncode, "seconds": round(time.monotonic()-started, 3),
                "stdout_tail": p.stdout[-5000:], "stderr_tail": p.stderr[-6000:]}
    except subprocess.TimeoutExpired:
        return {"exit": 124, "seconds": round(time.monotonic()-started, 3), "error": "TIMEOUT"}


def exercise(r):
    # Verify the exact original public source blob before experimenting.
    check = subprocess.run(["git", "hash-object", str(SOURCE)],
                           capture_output=True, text=True, check=True)
    r["original_source_git_blob"] = check.stdout.strip()
    if r["original_source_git_blob"] != EXPECTED_SOURCE_BLOB:
        raise RuntimeError("source changed; experiment must not silently target a new revision")
    with tempfile.TemporaryDirectory(prefix="iwxa-aider-real-source-") as d:
        root = Path(d) / "public-source-repair"
        root.mkdir()
        tests = root / "tests"
        tests.mkdir()
        shutil.copy2(SOURCE, root / "compare_frames.py")
        shutil.copy2(EXISTING_TEST, tests / "test_compare_frames.py")
        (tests / "test_p95_boundary.py").write_text(REGRESSION, encoding="utf-8")
        r["upstream_test_blob"] = subprocess.run(
            ["git", "hash-object", str(EXISTING_TEST)], capture_output=True,
            text=True, check=True
        ).stdout.strip()
        r["regression_original_sha256"] = sha256(tests / "test_p95_boundary.py")
        base_source = sha256(root / "compare_frames.py")
        base_test = sha256(tests / "test_compare_frames.py")
        env = dict(os.environ)
        env.update({
            "HOME": str(Path(d) / "home"),
            "XDG_CONFIG_HOME": str(Path(d) / "config"),
            "XDG_CACHE_HOME": str(Path(d) / "cache"),
            "OLLAMA_API_BASE": "http://127.0.0.1:11434",
            "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
            "AIDER_ANALYTICS_DISABLE": "true", "GIT_TERMINAL_PROMPT": "0",
            "PYTHONUTF8": "1",
            "PYTHONPATH": str(root),
        })
        for sub in ("home", "config", "cache"):
            (Path(d) / sub).mkdir()
        with urlopen("http://127.0.0.1:11434/api/tags", timeout=8) as response:
            models = json.load(response).get("models", [])
        r["local_model"] = [
            {k: m.get(k) for k in ("name", "digest", "size")}
            for m in models if m.get("name") == MODEL
        ]
        if not r["local_model"]:
            raise RuntimeError("model not installed")
        git = [
            ["git", "init", "-q"],
            ["git", "config", "user.name", "IWXA Public Research"],
            ["git", "config", "user.email", "research@example.invalid"],
            ["git", "add", "compare_frames.py", "tests/test_compare_frames.py",
             "tests/test_p95_boundary.py"],
            ["git", "commit", "-qm", "Frozen public Br-test benchmark and regression tests"],
        ]
        for args in git:
            x = cmd(args, root, env, 10)
            if x["exit"] != 0:
                raise RuntimeError("Git fixture setup failed: "+str(x))
        run_test = [sys.executable, "-m", "unittest", "discover", "-s", "tests",
                    "-p", "test_*.py", "-v"]
        r["baseline"] = cmd(run_test, root, env, 20)
        if r["baseline"]["exit"] == 0:
            raise AssertionError("baseline unexpectedly passed")
        if "test_p95_nearest_rank" not in r["baseline"]["stderr_tail"]:
            raise AssertionError("baseline failure was unrelated to p95 regression")
        r["version"] = cmd(["aider", "--version"], root, env, 15)
        if r["version"]["exit"] or AIDER_VERSION not in r["version"]["stdout_tail"]:
            raise RuntimeError("wrong Aider version")
        r["aider"] = cmd([
            "aider", "--model", "ollama_chat/"+MODEL, "--edit-format", "diff",
            "--no-auto-commits", "--no-auto-lint", "--no-stream",
            "--no-analytics", "--no-check-update", "--no-show-model-warnings",
            "--no-fancy-input", "--no-suggest-shell-commands", "--map-tokens", "0",
            "--yes-always", "--message", TASK, "compare_frames.py"
        ], root, env, 210)
        r["source_modified"] = sha256(root / "compare_frames.py") != base_source
        r["existing_tests_unchanged"] = sha256(tests / "test_compare_frames.py") == base_test
        r["new_tests_unchanged"] = (
            sha256(tests / "test_p95_boundary.py") == r["regression_original_sha256"]
        )
        diff_names = cmd(["git","diff","--name-only"], root, env, 12)
        r["modified_tracked_paths"] = [x for x in diff_names["stdout_tail"].splitlines() if x]
        r["git_patch"] = cmd(["git","diff","--","compare_frames.py"],root,env,12)["stdout_tail"]
        r["post_test"] = cmd(run_test,root,env,25)
        r["status"] = "PASS" if (
            r["aider"]["exit"] == 0
            and r["source_modified"]
            and r["modified_tracked_paths"] == ["compare_frames.py"]
            and r["existing_tests_unchanged"] and r["new_tests_unchanged"]
            and r["post_test"]["exit"] == 0
        ) else "FAIL"


def main():
    r = {
        "program": "Aider", "version_expected": AIDER_VERSION,
        "model": MODEL, "platform": platform.platform(),
        "scope": "EXISTING_PUBLIC_BR_TEST_SOURCE_P95_FIX_WITH_LOCAL_LLM",
        "iwxa_private_integration": "NOT_TESTED",
        "status": "FAIL",
    }
    try:
        exercise(r)
    except Exception as exc:
        r["error"] = type(exc).__name__+": "+str(exc)[:450]
        r["traceback_tail"] = traceback.format_exc()[-1600:]
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding="utf-8")
    print("IWXA_AIDER_REAL_SOURCE",json.dumps(r,ensure_ascii=False))
    return 0 if r["status"]=="PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
