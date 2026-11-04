"""Real pinned jq -> native Tauri command IPC -> approved removal, Windows CI.

No actual product data or secrets. Uses the already qualified generic installer.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"universal-install"))
from iwxa_universal_install import (
    ManagedArtifactBackend,ManagedArtifactSpec,ApprovedInstall,ApprovedRemoval
)
from iwxa_universal_install.managed import _native_platform

def qualify():
    platform=_native_platform()
    if platform!="windows-x86_64":
        raise RuntimeError("TAURI_IPC_WINDOWS_X64_ONLY")
    catalog=json.loads((ROOT/"universal-install/catalog/jq-1.8.1.json").read_text(encoding="utf-8"))
    selected=catalog["platforms"][platform]
    spec=ManagedArtifactSpec(
        program_id="JQ_CLI_V1",slug=catalog["slug"],
        release_id=catalog["upstream_tag"],platform=platform,
        executable_name=selected["executable"],archive_name=selected["asset"],
        archive_format="binary",archive_sha256=selected["sha256"],
        executable_sha256=selected["sha256"],
        download_url="https://github.com/jqlang/jq/releases/download/"+
            catalog["upstream_tag"]+"/"+selected["asset"])
    backend=ManagedArtifactBackend()
    with tempfile.TemporaryDirectory(prefix="iwxa-tauri-ipc-") as directory:
        root=Path(directory)
        stage=root/"stage"
        target=root/"installed"
        target.mkdir()
        user_file=target/"independent-user-data.txt"
        user_file.write_text("preserve",encoding="utf-8")
        backend.prepare(spec,str(stage))
        approval=ApprovedInstall(spec.program_id,spec.fingerprint,
                                 "PINS-tauri-ci-install",str(target),True)
        applied=backend.apply(spec,str(stage),approval)
        if not applied.installed:
            raise RuntimeError("TAURI_IPC_INSTALL_FAILED")
        try:
            env={**os.environ,"IWXA_CLI_EXECUTABLE":str(applied.executable),
                 "IWXA_CLI_CATALOG":str(ROOT/"universal-install/catalog/cli-connectors-1.json")}
            crate=ROOT/"universal-install/qualification/tauri-ipc/Cargo.toml"
            result=subprocess.run(
                ["cargo","test","--manifest-path",str(crate),
                 "--test","ipc_real","--","--nocapture"],
                capture_output=True,text=True,timeout=1200,
                cwd=ROOT,env=env,check=False)
            print(result.stdout[-10000:],flush=True)
            if result.returncode!=0:
                raise RuntimeError("TAURI_IPC_TEST_FAILED: "+result.stderr[-3000:])
            if "IWXA_TAURI_NATIVE_IPC" not in result.stdout:
                raise RuntimeError("TAURI_IPC_EVIDENCE_MISSING")
        finally:
            removed=ApprovedRemoval(spec.program_id,spec.fingerprint,
                                    "PINS-tauri-ci-remove",str(target),True)
            if backend.remove(spec,removed).status!="REMOVED":
                raise RuntimeError("TAURI_IPC_REMOVE_FAILED")
            if user_file.read_text(encoding="utf-8")!="preserve":
                raise RuntimeError("TAURI_IPC_USER_DATA_CHANGED")
        print(json.dumps({"schema":"IWXA_TAURI_NATIVE_IPC_QUALIFICATION/1",
              "status":"PASS","platform":platform,"real_jq":True,
              "real_tauri_rust_ipc_command":True,
              "mock_tauri_runtime":True,"installed_gui_qualified":False,
              "user_files_preserved":True},sort_keys=True))

if __name__=="__main__":
    qualify()
