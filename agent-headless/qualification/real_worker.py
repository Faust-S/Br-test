"""Real Obscura worker qualification; requires a locally BUILT pinned binary.

No fixture or mock is accepted as evidence. Tests independent live HTTPS URLs
and records actual content digest, HTTP status, elapsed wall time and peak RSS
sampling when psutil is available. The result is not a web-compatibility verdict.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import os
import statistics
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from iwxa_obscura.worker import ObscuraWorkerController
from iwxa_obscura.controller import approved_url, ObscuraControllerError


def _sample_children(stop, values):
    try:
        import psutil
    except ImportError:
        return
    me = psutil.Process(os.getpid())
    while not stop.is_set():
        total = 0
        try:
            for child in me.children(recursive=True):
                try:
                    total += child.memory_info().rss
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        values.append(total)
        stop.wait(0.02)


def qualify(binary, sha256, urls, *, iterations, timeout):
    result = {
        "schema": "IWXA_OBSCURA_REAL_WORKER_QUALIFICATION/1.0",
        "run_at": datetime.now(timezone.utc).isoformat(),
        "platform": sys.platform,
        "binary": str(binary), "binary_sha256_expected": sha256,
        "requires_real_patched_worker": True,
        "network_sandbox_verified": False,
        "sites": [],
    }
    for url in urls:
        origin = "https://" + urlsplit(url).hostname
        worker = ObscuraWorkerController(
            binary=binary, binary_sha256=sha256,
            allowed_origins=frozenset({origin}),
            timeout_seconds=timeout, max_output_bytes=1_500_000)
        observed = []
        for _ in range(iterations):
            stop = threading.Event()
            samples = []
            sampler = threading.Thread(target=_sample_children, args=(stop, samples), daemon=True)
            start = time.monotonic()
            sampler.start()
            try:
                output = worker.observe(url)
                datum = {
                    "state": "OBSERVED",
                    "http_status": output.http_status,
                    "final_url": output.final_url,
                    "content_sha256": output.content_sha256,
                    "content_bytes": output.content_bytes,
                    "engine_elapsed_ms": output.elapsed_ms,
                    "title": output.page_title[:160],
                }
            except ObscuraControllerError as error:
                datum = {"state": "REJECTED", "error_code": error.code}
            finally:
                stop.set()
                sampler.join(timeout=1)
            datum["wall_elapsed_ms"] = round((time.monotonic() - start) * 1000)
            datum["peak_sampled_child_rss_bytes"] = max(samples) if samples else None
            datum["rss_quality"] = "20ms sampling, lower bound" if samples else "unavailable"
            observed.append(datum)
        success = [x["wall_elapsed_ms"] for x in observed if x["state"] == "OBSERVED"]
        result["sites"].append({
            "url": url, "runs": observed,
            "successful_runs": len(success),
            "median_success_wall_ms": statistics.median(success) if success else None,
        })
    return result


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--binary", type=Path, required=True)
    ap.add_argument("--sha256", required=True)
    ap.add_argument("--url", action="append", required=True)
    ap.add_argument("--iterations", type=int, default=3)
    ap.add_argument("--timeout", type=float, default=45)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args(argv)
    if not 1 <= args.iterations <= 10 or not 1 <= args.timeout <= 60:
        ap.error("limits exceeded")
    if not 2 <= len(args.url) <= 12:
        ap.error("2-12 independent sites required")
    origins = set()
    for url in args.url:
        try:
            origin = "https://" + urlsplit(url).hostname
            approved_url(url, frozenset({origin}))
        except (TypeError, ValueError, ObscuraControllerError):
            ap.error("public HTTPS URLs only")
        origins.add(origin)
    if len(origins) < 2:
        ap.error("sites must have at least two different origins")
    report = qualify(args.binary, args.sha256, args.url,
                     iterations=args.iterations, timeout=args.timeout)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd, staged = tempfile.mkstemp(prefix=".qualification-", suffix=".json", dir=args.output.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(report, file, indent=2, ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(staged, args.output)
    finally:
        if os.path.exists(staged):
            os.unlink(staged)
    print(json.dumps({"report": str(args.output),
                      "success_counts": [x["successful_runs"] for x in report["sites"]]}))
    return 0 if all(x["successful_runs"] == args.iterations for x in report["sites"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
