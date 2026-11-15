"""Real benign browser DOM click probe. No server-side mutation is asserted."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from iwxa_obscura.interaction import ObscuraInteractiveController, ObscuraInteractionError

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--worker",type=Path,required=True)
    ap.add_argument("--url",required=True)
    ap.add_argument("--selector",required=True)
    ap.add_argument("--confirmation-selector",required=True)
    ap.add_argument("--expected-text",required=True)
    ap.add_argument("--expected-final-url")
    ap.add_argument("--output",type=Path,required=True)
    a=ap.parse_args()
    worker=a.worker.resolve(strict=True)
    digest=hashlib.sha256(worker.read_bytes()).hexdigest()
    origin="https://"+urlsplit(a.url).hostname
    controller=ObscuraInteractiveController(
        binary=worker,binary_sha256=digest,
        allowed_origins=frozenset({origin}),
        timeout_seconds=45,max_output_bytes=1_000_000,
    )
    try:
        result=controller.click_control(
            a.url,selector=a.selector,confirmation_selector=a.confirmation_selector,
            expected_text=a.expected_text,
        )
        navigated = bool(result["navigation_observed"])
        final_matches = a.expected_final_url is None or (
            navigated and result["final_url"] == a.expected_final_url)
        record={
            "status":"DOM_NAVIGATION_OBSERVED" if a.expected_final_url and final_matches
                     else ("DOM_POSTCONDITION_OBSERVED" if final_matches else "HOLD"),
            "failure_code":None if final_matches else "DOM_NAVIGATION_NOT_OBSERVED",
            "remote_mutation_verified":False,
            "external_effect_state":result["external_effect_state"],
            "observed_text_sha256":result["observed_text_sha256"],
            "final_url":result["final_url"],
            "navigation_observed":navigated,
            "elapsed_ms":result["elapsed_ms"],
            "worker_sha256":digest,
        }
    except ObscuraInteractionError as exc:
        record={
            "status":"HOLD",
            "failure_code":exc.code,
            "external_effect_state":exc.external_effect_state,
            "remote_mutation_verified":False,
            "worker_sha256":digest,
        }
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(record,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(record))
    return 0 if record["status"] in ("DOM_POSTCONDITION_OBSERVED", "DOM_NAVIGATION_OBSERVED") else 2

if __name__=="__main__":
    raise SystemExit(main())
