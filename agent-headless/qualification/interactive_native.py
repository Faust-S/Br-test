"""Native qualification of Obscura headless navigation and DOM (no GUI).

Requires a real pinned worker executable, public HTTPS network, and an explicit
SHA256. No synthetic worker is accepted; this does not qualify remote writes.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from iwxa_obscura.interaction import ObscuraInteractiveController
from iwxa_obscura.controller import ObscuraControllerError


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def qualify(binary: Path, pin: str, *, pages: list[str], link_url: str,
            link_selector: str, iterations: int, timeout: float) -> dict:
    if not binary.is_file() or binary.stat().st_size < 1024 * 1024:
        raise RuntimeError("REAL_NATIVE_WORKER_REQUIRED")
    if digest(binary) != pin:
        raise RuntimeError("BINARY_DIGEST_MISMATCH")
    rows = []
    for page in pages:
        host = page.split("/")[2]
        controller = ObscuraInteractiveController(
            binary=binary, binary_sha256=pin,
            allowed_origins=frozenset({"https://" + host}),
            timeout_seconds=timeout, max_output_bytes=1_000_000,
        )
        for repeat in range(iterations):
            started = time.monotonic()
            try:
                observed = controller.observe(page)
                text = controller.inspect(page, selector="body")
                assert observed.http_status in range(200, 400)
                assert text["text_bytes"] > 0 and text["http_status"] in range(200, 400)
                rows.append({
                    "url": page, "iteration": repeat, "passed": True,
                    "fetch_http_status": observed.http_status,
                    "fetch_sha256": observed.content_sha256,
                    "dom_text_sha256": text["text_sha256"],
                    "fetch_elapsed_ms": observed.elapsed_ms,
                    "dom_elapsed_ms": text["elapsed_ms"],
                    "wall_ms": round((time.monotonic() - started) * 1000),
                })
            except (ObscuraControllerError, AssertionError) as error:
                rows.append({
                    "url": page, "iteration": repeat, "passed": False,
                    "failure_code": getattr(error, "code", "EMPTY_OR_BAD_HTTP"),
                    "wall_ms": round((time.monotonic() - started) * 1000),
                })
    follow = {"passed": False}
    controller = ObscuraInteractiveController(
        binary=binary, binary_sha256=pin,
        allowed_origins=frozenset({"https://" + link_url.split("/")[2]}),
        timeout_seconds=timeout, max_output_bytes=1_000_000,
    )
    try:
        result = controller.follow_link(link_url, selector=link_selector)
        follow = {
            "passed": result["http_status"] in range(200, 400)
                      and result["content_bytes"] > 0
                      and result["selected_href"] != result["source_final_url"],
            "source_final_url": result["source_final_url"],
            "selected_href": result["selected_href"],
            "final_url": result["final_url"],
            "http_status": result["http_status"],
            "content_sha256": result["content_sha256"],
            "elapsed_ms": result["elapsed_ms"],
        }
    except ObscuraControllerError as error:
        follow = {"passed": False, "failure_code": error.code}
    times = [x["fetch_elapsed_ms"] for x in rows if x["passed"]]
    return {
        "schema": "IWXA_OBSCURA_AGENT06_NATIVE_QUALIFICATION/1.0",
        "platform": sys.platform, "binary_sha256": pin,
        "independent_https_pages": len(pages), "iterations": iterations,
        "navigation_and_dom": rows, "follow_link": follow,
        "median_fetch_elapsed_ms": statistics.median(times) if times else None,
        "cold_worker_each_operation": True, "gui": False,
        "remote_write_accepted": False,
        "status": "PASS" if all(x["passed"] for x in rows) and follow["passed"] else "HOLD",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--page", action="append", required=True)
    parser.add_argument("--link-url", required=True)
    parser.add_argument("--link-selector", required=True)
    parser.add_argument("--iterations", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=45)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.iterations <= 5 or not 1 <= args.timeout <= 60:
        parser.error("invalid test limits")
    if len(set(args.page)) < 2:
        parser.error("two distinct public pages required")
    report = qualify(
        args.worker.resolve(), args.sha256,
        pages=args.page, link_url=args.link_url,
        link_selector=args.link_selector,
        iterations=args.iterations, timeout=args.timeout,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "checks": len(report["navigation_and_dom"]),
                      "passed": sum(x["passed"] for x in report["navigation_and_dom"]),
                      "follow": report["follow_link"]["passed"]}))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
