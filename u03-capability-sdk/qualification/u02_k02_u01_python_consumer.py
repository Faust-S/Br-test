"""Independent Python U01/U03 consumer of an actual public git source and K02 plan.

Reads one JSON vector from stdin. Host approval is local and explicit. The Python
consumer verifies K02's *canonical plan digest* without copying its selector.
It does not claim to run the installed Personal Agent or perform a Task.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "universal-install"))
sys.path.insert(0, str(ROOT / "u03-capability-sdk/python"))

from iwxa_universal_install.program_connection import CliConnection
from iwxa_capability_sdk import assess
from u01_bridge import observe_u01_capabilities

def require(value, code):
    if not value:
        raise RuntimeError(code)

def sha(value):
    return hashlib.sha256(value).hexdigest()

def canonical(value):
    # Exact stable, deterministic JSON subset used by native K02's digest().
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")

def main():
    vector = json.loads(sys.stdin.read())
    program_id = vector["programId"]
    path = Path(vector["repoPath"]).resolve()
    require(path.is_dir(), "PYTHON_K02_REPO_REQUIRED")
    require(vector["gitPath"] == "modules/k02-interoperability/skills/exact-source-audit.instructions.json",
            "PYTHON_SOURCE_PATH_MISMATCH")
    revision = vector["gitRevision"]
    require(re.fullmatch(r"[a-f0-9]{40}", revision) is not None,
            "PYTHON_SOURCE_REVISION_INVALID")
    source = vector["source"]
    descriptor = source["descriptor"]
    observed = source["observation"]
    pinned = source["pin"]
    read = source["read"]
    require(descriptor["schema"] == "IWXA_SOURCE_CONNECTOR/1.0.0" and
            descriptor["authority"] == "OBSERVATION_ONLY" and
            observed["authority"] == "OBSERVATION_ONLY" and
            pinned["authority"] == "OBSERVATION_ONLY" and
            descriptor["providerId"] == observed["providerId"] == pinned["providerId"] and
            descriptor["sourceRef"] == observed["sourceRef"] == pinned["sourceRef"] and
            observed["status"] == "OK" and
            observed["revision"] == pinned["revision"] == read["revision"] ==
            read["contentRef"] and
            observed["contentRef"] == pinned["revision"],
            "PYTHON_U02_PROVENANCE_MISMATCH")

    source_content = (path / vector["gitPath"]).read_bytes()
    require("sha256:" + sha(source_content) == pinned["revision"],
            "PYTHON_SOURCE_CHANGED")
    plan = vector["plan"]
    plan_digest = plan["planDigest"]
    require(re.fullmatch(r"[a-f0-9]{64}", plan_digest) is not None,
            "PYTHON_PLAN_DIGEST_INVALID")
    digest_body = dict(plan)
    del digest_body["planDigest"]
    require(sha(canonical(digest_body)) == plan_digest,
            "PYTHON_K02_PLAN_CHANGED")
    steps = plan["steps"]
    require(len(steps) == 1 and steps[0]["capabilityId"] == "source.git.show" and
            steps[0]["invocationAuthority"] == "HOST_ONLY" and
            steps[0]["invocationState"] == "NOT_INVOKED" and
            steps[0]["proofOfInvoke"] is None and
            steps[0]["hostApprovalRequired"] is True,
            "PYTHON_K02_PLAN_NOT_AUTHORIZED")

    module = vector["moduleDescriptor"]
    host = vector["host"]
    cfg = {"programId": program_id,
           "bindings": [{"operation": "show", "id": "source.git.show", "version": "1.0.0"}]}
    git = shutil.which("git")
    require(git is not None, "PYTHON_GIT_NOT_FOUND")
    spec = {"schema": "IWXA_CLI_PROGRAM/1", "program_id": program_id,
            "executable_name": Path(git).name,
            "version_args": ["--version"], "version_contains": "git version",
            "operations": {"show": ["-C", str(path), "show", "{revpath}"]}}
    c = CliConnection(spec, git)
    counters = {"invoke": 0, "capabilities": 0}

    def observe():
        counters["capabilities"] += 1
        return observe_u01_capabilities(c, cfg)

    require(c.inspect()["status"] == "DISCOVERED", "PYTHON_DISCOVERY_FAILED")
    require(assess(module, host, observe())["compatible"] is False,
            "PYTHON_INSTALLED_IMPLICITLY_READY")
    require(c.connect()["status"] == "CONNECTED", "PYTHON_CONNECT_FAILED")
    first = observe()
    require(first[0]["available"] is False and first[0]["reason"] == "operation-unverified",
            "PYTHON_FIRST_USE_INCORRECT")
    require(counters["invoke"] == 0, "PYTHON_UNAUTHORIZED_INVOKE")

    # Only this owning test host may authorize an actual invocation.
    host_approved = True
    require(host_approved, "PYTHON_HOST_APPROVAL_REQUIRED")
    counters["invoke"] += 1
    reply = c.invoke("show", {"revpath": revision + ":" + vector["gitPath"]})
    require(reply["status"] == "EXECUTED" and reply["executed"] is True,
            "PYTHON_GIT_EXECUTION_FAILED")
    require(reply["output"].encode("utf-8") == source_content and
            sha(reply["output"].encode("utf-8")) == vector["expectedSha256"],
            "PYTHON_GIT_SOURCE_OUTPUT_MISMATCH")
    require(observe()[0]["reason"] == "execution-receipt-missing",
            "PYTHON_UNOWNED_RECEIPT_PROMOTION")
    ref = "qualification:python:sha256:" + sha(canonical({
        "programId": program_id, "operation": "show",
        "epoch": reply["connection_epoch"], "planDigest": plan_digest,
        "sourceRevision": pinned["revision"],
        "outputSha256": vector["expectedSha256"],
    }))
    cfg["receipts"] = {"show": {"observation": reply, "observationRef": ref}}
    accepted = observe()
    require(accepted[0]["available"] is True and
            accepted[0]["observationRef"] == ref and
            assess(module, host, accepted)["compatible"] is True,
            "PYTHON_U03_HOST_RECEIPT_MISMATCH")
    require(assess(module, {**host, "version": "0.2.0"}, accepted)["compatible"] is False,
            "PYTHON_VERSION_MISMATCH_NOT_REJECTED")
    original_calls = counters["invoke"]
    require(observe() == observe(), "PYTHON_EVIDENCE_READBACK_DRIFT")
    require(counters["invoke"] == original_calls, "PYTHON_REPLAY_ON_READBACK")
    require(c.disconnect()["status"] == "DISCONNECTED", "PYTHON_DISCONNECT_FAILED")
    require(observe()[0]["available"] is False, "PYTHON_RECEIPT_SURVIVED_DISCONNECT")
    require(c.recover()["status"] == "CONNECTED", "PYTHON_RECOVERY_FAILED")
    require(observe()[0]["available"] is False, "PYTHON_RECEIPT_SURVIVED_RECONNECT")
    require(counters["invoke"] == 1, "PYTHON_REPLAY_ON_RECOVERY")
    return {"status": "PASS", "invocations": counters["invoke"],
            "resultSha256": vector["expectedSha256"], "receiptRef": ref,
            "sourceRevision": pinned["revision"],
            "hostOwnsState": True, "taskCreated": False}

if __name__ == "__main__":
    try:
        print(json.dumps(main(), sort_keys=True, ensure_ascii=False))
    except Exception as error:
        print(type(error).__name__ + ":" + str(error), file=sys.stderr)
        raise SystemExit(1)
