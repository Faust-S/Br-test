"""End-to-end *public HTTPS* download/install/execute/remove qualification.

Requires an explicitly TEST-ONLY prerelease manifest. Never uses user profiles,
a private Agent, or an installed browser. Uses GitHub Release URLs via the real
pinned preparer and the same universal backend used by embedding products.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import platform
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "universal-install"))
sys.path.insert(0, str(ROOT / "agent-headless"))
from iwxa_obscura.preparer import ArtifactSpec, GUARD_PROTOCOL
from iwxa_obscura.handshake import require_worker_greeting
from iwxa_universal_install import ApprovedInstall, ApprovedRemoval, ObscuraManagedBackend

def native_platform() -> str:
    arch = platform.machine().lower()
    arch = {"amd64": "x86_64", "x86_64": "x86_64", "aarch64": "arm64", "arm64": "arm64"}[arch]
    os_family = "windows" if os.name == "nt" else ("macos" if sys.platform == "darwin" else "linux")
    return os_family + "-" + arch

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--expected-source-sha", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    record = json.loads(args.manifest.read_text(encoding="utf-8"))
    if record.get("schema") != "IWXA_NATIVE_TEST_RELEASE/1.0" or record.get("intent") != "CI_ONLY_NO_END_USER_RELEASE":
        raise RuntimeError("NETWORK_TEST_MANIFEST_NOT_QUALIFIED")
    if record.get("native_source_head_sha") != args.expected_source_sha:
        raise RuntimeError("NETWORK_TEST_WRONG_SOURCE_COMMIT")
    current = native_platform()
    spec = ArtifactSpec.from_mapping(record["platforms"][current])
    if not spec.download_url.startswith("https://github.com/Faust-S/Br-test/releases/download/"):
        raise RuntimeError("NETWORK_TEST_UNTRUSTED_RELEASE")

    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="iwxa-public-https-") as tmp:
        work = Path(tmp).resolve()
        backend = ObscuraManagedBackend()
        install_root = work / "installed"
        staged_root = work / "download-and-stage"
        updates: list[tuple[int, int | None]] = []
        staged = backend.prepare(spec, str(staged_root),
                                 on_progress=lambda n, total: updates.append((n, total)))
        if staged.status != "STAGED_NOT_ACTIVATED" or not updates or updates[-1][0] <= 0:
            raise RuntimeError("NETWORK_TEST_DOWNLOAD_INCOMPLETE")
        if backend.verify(spec, str(install_root)).installed:
            raise RuntimeError("NETWORK_TEST_PREMATURE_INSTALL")
        approval = ApprovedInstall(backend.program_id, spec.fingerprint,
                                   "PINS-public-https-install", str(install_root), True)
        result = backend.apply(spec, str(staged_root), approval)
        if not result.installed or result.connected or result.receipt_written:
            raise RuntimeError("NETWORK_TEST_INSTALL_OBSERVATION_INVALID")
        executable = Path(result.executable)
        if sha256_file(executable) != spec.executable_sha256:
            raise RuntimeError("NETWORK_TEST_BINARY_SHA_MISMATCH")
        profile = work / "runtime"
        profile.mkdir()
        env = {k: v for k,v in os.environ.items()
               if k in ("SystemRoot", "WINDIR", "PATH", "LANG", "TZ")}
        env.update({"HOME":str(profile),"USERPROFILE":str(profile),
                    "APPDATA":str(profile),"LOCALAPPDATA":str(profile),
                    "TEMP":str(profile),"TMP":str(profile),"TMPDIR":str(profile),
                    "IWXA_OBSCURA_ALLOWED_ORIGIN":"https://example.com"})
        creation = {"creationflags":subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session":True}
        attestation = subprocess.run([str(executable),"--iwxa-attest-origin-guard"],
                                     capture_output=True, timeout=20, env=env, **creation)
        if attestation.returncode != 0 or attestation.stdout.strip() != b"IWXA_ORIGIN_NET_GUARD_V1_NO_RENDER":
            raise RuntimeError("NETWORK_TEST_WORKER_ATTESTATION_FAILED")
        p = subprocess.Popen([str(executable)], cwd=profile, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, **creation)
        try:
            require_worker_greeting(p.stdout, deadline=time.monotonic()+30)
            out,err = p.communicate(input=b'{"cmd":"shutdown"}\n', timeout=20)
            if p.returncode != 0 or not any(json.loads(v).get("result")=="bye"
                                               for v in out.splitlines() if v):
                raise RuntimeError("NETWORK_TEST_SHUTDOWN_FAILED")
        finally:
            if p.poll() is None:
                p.kill()
                p.communicate(timeout=5)
        removal = ApprovedRemoval(backend.program_id,spec.fingerprint,
                                  "PINS-public-https-remove",str(install_root),True)
        if backend.remove(spec,removal).status != "REMOVED" or backend.recover(spec,str(install_root)).installed:
            raise RuntimeError("NETWORK_TEST_REMOVE_FAILED")
        report = {
            "schema":"IWXA_PUBLIC_HTTPS_LIFECYCLE/1",
            "status":"PASS","platform":current,"source_head":args.expected_source_sha,
            "archive_sha256":spec.archive_sha256,
            "installed_sha256":spec.executable_sha256,
            "download_finished_bytes":updates[-1][0],
            "progress_events":len(updates),
            "attestation":"IWXA_ORIGIN_NET_GUARD_V1_NO_RENDER",
            "greeting":GUARD_PROTOCOL,
            "installed_from_network":True,
            "executed_from_installed_path":True,
            "removed_from_installed_path":True,
            "private_agent_involved":False,
            "duration_ms":int((time.monotonic()-started)*1000),
        }
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
