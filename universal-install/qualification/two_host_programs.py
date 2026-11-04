"""Real install -> two independent hosts -> invoke -> reconnect -> remove.

Uses official pinned jq/yq binaries, never executes a mock program.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"universal-install"))
from iwxa_universal_install import (
    ApprovedInstall, ApprovedRemoval, ManagedArtifactBackend, ManagedArtifactSpec
)
from iwxa_universal_install.managed import _native_platform
from iwxa_universal_install.program_connection import CliConnection

CATALOG=ROOT/"universal-install"/"catalog"
CONNECTORS=CATALOG/"cli-connectors-1.json"
REPO={"JQ_CLI_V1":"jqlang/jq","YQ_CLI_V1":"mikefarah/yq"}

def qualify():
    platform=_native_platform()
    connector_catalog=json.loads(CONNECTORS.read_text(encoding="utf-8"))
    report=[]
    with tempfile.TemporaryDirectory(prefix="iwxa-program-integration-") as temp:
        root=Path(temp).resolve()
        install_root=root/"software"
        stage=root/"stage"
        install_root.mkdir()
        (install_root/"user-data.txt").write_text("preserve",encoding="utf-8")
        for name,filename in (("JQ_CLI_V1","jq-1.8.1.json"),("YQ_CLI_V1","yq-4.54.1.json")):
            catalog=json.loads((CATALOG/filename).read_text(encoding="utf-8"))
            selected=catalog["platforms"].get(platform)
            if not selected:
                if name=="JQ_CLI_V1" and platform=="windows-arm64":
                    report.append({"program_id":name,"status":"UPSTREAM_UNSUPPORTED"})
                    continue
                raise RuntimeError("EXPECTED_PLATFORM_ASSET_MISSING:"+name)
            manifest=next(p for p in connector_catalog["programs"] if p["program_id"]==name)
            native_name=selected["executable"]
            manifest={**manifest,"executable_name":native_name}
            package=ManagedArtifactSpec(
                program_id=name,slug=catalog["slug"],release_id=catalog["upstream_tag"],
                platform=platform,executable_name=native_name,
                archive_name=selected["asset"],archive_format="binary",
                archive_sha256=selected["sha256"],executable_sha256=selected["sha256"],
                download_url="https://github.com/"+REPO[name]+"/releases/download/"+
                             catalog["upstream_tag"]+"/"+selected["asset"]
            )
            backend=ManagedArtifactBackend()
            prepared=backend.prepare(package,str(stage))
            if prepared.status!="STAGED_NOT_ACTIVATED":
                raise RuntimeError("PREPARE_NOT_STAGED")
            approved=ApprovedInstall(name,package.fingerprint,
                                      "PINS-connector-install",str(install_root),True)
            installed=backend.apply(package,str(stage),approved)
            if not installed.installed:
                raise RuntimeError("INSTALL_FAILED")
            program=Path(installed.executable)
            # A prefix such as jq-1.8 must not qualify jq-1.8.1.
            prefix_spec={**manifest,"version_contains":manifest["version_contains"].rsplit(".",1)[0]}
            if CliConnection(prefix_spec,str(program)).connect()["status"]!="VERSION_UNVERIFIED":
                raise RuntimeError("VERSION_PREFIX_FALSE_POSITIVE")
            connection=CliConnection(manifest,str(program))
            if connection.inspect()["status"]!="DISCOVERED":
                raise RuntimeError("DISCOVERY_FAILED")
            connected=connection.connect()
            if connected["status"]!="CONNECTED":
                raise RuntimeError("PYTHON_CONNECT_FAILED")
            capabilities=connection.capabilities()
            if capabilities["operations"]!=[{"name":"evaluate","declared":True,"verified":False}]:
                raise RuntimeError("CAPABILITY_UNVERIFIED_LABEL_INVALID")
            direct_start=time.perf_counter()
            direct=subprocess.run([str(program),"-n","6 * 7"],capture_output=True,
                                  text=True,timeout=20,check=True)
            direct_ms=(time.perf_counter()-direct_start)*1000
            first=connection.invoke("evaluate",{"expression":"6 * 7"})
            if first["status"]!="EXECUTED" or first["output"].strip()!=direct.stdout.strip() or first["output"].strip()!="42":
                raise RuntimeError("PYTHON_REAL_INVOKE_FAILED")
            alternate=connection.invoke("evaluate",{"expression":"7 * 8"})
            if alternate["status"]!="EXECUTED" or alternate["output"].strip()!="56":
                raise RuntimeError("DYNAMIC_PARAMETER_NOT_EXECUTED")
            if not connection.capabilities()["operations"][0]["verified"]:
                raise RuntimeError("CAPABILITY_NOT_VERIFIED_AFTER_REAL_EXECUTION")
            if connection.invoke("not_declared")["status"]!="OPERATION_UNSUPPORTED":
                raise RuntimeError("UNDECLARED_OPERATION_ACCEPTED")
            connection.disconnect()
            if connection.invoke("evaluate",{"expression":"6 * 7"})["status"]!="NOT_CONNECTED":
                raise RuntimeError("DISCONNECTED_INVOKE_ACCEPTED")
            if connection.recover()["status"]!="CONNECTED":
                raise RuntimeError("RECONNECT_FAILED")
            connection.disconnect()
            node_start=time.perf_counter()
            node=subprocess.run(["node",str(ROOT/"universal-install"/"qualification"/"node_host.mjs"),
                                 str(CONNECTORS),name,str(program)],
                                 capture_output=True,text=True,timeout=35,check=True)
            node_ms=(time.perf_counter()-node_start)*1000
            node_report=json.loads(node.stdout.strip())
            if node_report["status"]!="PASS" or node_report["output"]!="42":
                raise RuntimeError("NODE_REAL_INVOKE_FAILED")
            host_test=subprocess.run(
                ["node",str(ROOT/"universal-install"/"qualification"/"product_host_ports.mjs"),
                 str(CONNECTORS),name,str(program)],
                capture_output=True,text=True,timeout=30,check=True)
            if json.loads(host_test.stdout.strip())["status"]!="PASS":
                raise RuntimeError("PRODUCT_HOST_PORT_FAILED")
            rust_binary = (ROOT/"universal-install"/"host-bindings"/"rust-port"/
                           "target"/"debug"/("iwxa-rust-cli-qualification.exe"
                                            if sys.platform=="win32"
                                            else "iwxa-rust-cli-qualification"))
            rust_qualified=False
            rust_binary_bytes=None
            if rust_binary.exists():
                rust = subprocess.run(
                    [str(rust_binary),str(CONNECTORS),name,str(program)],
                    capture_output=True,text=True,timeout=35,check=True)
                rust_report=json.loads(rust.stdout.strip())
                if rust_report["status"]!="PASS" or rust_report["value"]!="42":
                    raise RuntimeError("NATIVE_RUST_HOST_FAILED")
                rust_qualified=True
                rust_binary_bytes=rust_binary.stat().st_size
                print(json.dumps({"schema":"IWXA_RUST_REAL_PROGRAM_EVIDENCE/1",
                                  "status":"PASS","program_id":name,
                                  "platform":platform,"binary_bytes":rust_binary_bytes,
                                  "rust_without_python_core":True,
                                  "real_executable":True}), flush=True)
            elif __import__("os").environ.get("IWXA_REQUIRE_RUST_HOST") == "1":
                raise RuntimeError("NATIVE_RUST_HOST_NOT_BUILT")
            if not backend.recover(package,str(install_root)).installed:
                raise RuntimeError("INSTALL_READBACK_FAILED")
            approved_remove=ApprovedRemoval(name,package.fingerprint,
                                             "PINS-connector-remove",str(install_root),True)
            if backend.remove(package,approved_remove).status!="REMOVED":
                raise RuntimeError("REMOVE_FAILED")
            if backend.recover(package,str(install_root)).installed:
                raise RuntimeError("REMOVE_READBACK_FAILED")
            if (install_root/"user-data.txt").read_text(encoding="utf-8")!="preserve":
                raise RuntimeError("USER_DATA_DAMAGED")
            report.append({"program_id":name,"status":"PASS","two_hosts":True,
                           "platform":platform,"direct_ms":round(direct_ms,3),
                           "python_invoke_ms":first["elapsed_ms"],
                           "node_process_with_connection_ms":round(node_ms,3),
                           "rust_host_qualified":rust_qualified,
                           "rust_binary_bytes":rust_binary_bytes,
                           "download_bytes":package.archive_sha256 is not None})
    print(json.dumps({"schema":"IWXA_PROGRAM_INTEGRATION_QUALIFICATION/1",
                      "status":"PASS","platform":platform,"results":report},sort_keys=True))

if __name__=="__main__":
    qualify()
