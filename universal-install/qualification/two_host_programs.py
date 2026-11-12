"""Real install -> two independent hosts -> invoke -> reconnect -> remove.

Uses official pinned jq/yq binaries, never executes a mock program.
"""
import hashlib
import json
import os
import shutil
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
            original_stat=program.stat()
            reapproval=backend.apply(package,str(stage),approved)
            if not reapproval.installed or reapproval.executable!=str(program):
                raise RuntimeError("SECOND_INSTALL_NOT_REUSED")
            after_stat=program.stat()
            if (original_stat.st_size,original_stat.st_mtime_ns)!=(
                after_stat.st_size,after_stat.st_mtime_ns):
                raise RuntimeError("SECOND_INSTALL_REPLACED_EXISTING_BINARY")
            installed_binary_bytes=program.stat().st_size
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
            if capabilities["operations"] != [
                {"name":op,"declared":True,"verified":False}
                for op in sorted(manifest["operations"])
            ]:
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
            if not next(x for x in connection.capabilities()["operations"]
                        if x["name"]=="evaluate")["verified"]:
                raise RuntimeError("CAPABILITY_NOT_VERIFIED_AFTER_REAL_EXECUTION")
            if any(x["verified"] for x in connection.capabilities()["operations"]
                   if x["name"]!="evaluate"):
                raise RuntimeError("UNINVOKED_FILE_CAPABILITY_FALSELY_VERIFIED")
            # A user-selected source is actually read by the installed CLI.
            source_path = root / ("file source " + name)
            if name == "JQ_CLI_V1":
                source_path = source_path.with_suffix(".json")
                source_path.write_text('{"rows":[{"units":2,"price":19},{"units":3,"price":7}]}',
                                       encoding="utf-8")
                filter_path = root / "user-transform.jq"
                filter_path.write_text(".rows | map(.units * .price) | add\n",encoding="utf-8")
                document = connection.invoke("transform_json",{
                    "filter":str(filter_path),"input":str(source_path)})
                if document["status"]!="EXECUTED" or document["output"].strip()!="59":
                    raise RuntimeError("JQ_REAL_FILE_OPERATION_FAILED")
            else:
                source_path = source_path.with_suffix(".yaml")
                source_path.write_text('rows:\n  - units: 2\n    price: 19\n'
                                       '  - units: 3\n    price: 7\n',encoding="utf-8")
                document = connection.invoke("read_yaml_json",{"input":str(source_path)})
                if document["status"]!="EXECUTED" or json.loads(document["output"])!={
                    "rows":[{"units":2,"price":19},{"units":3,"price":7}]}:
                    raise RuntimeError("YQ_REAL_FILE_OPERATION_FAILED")
            file_readback = root / ("file-output-" + name + ".txt")
            # Text subprocess output can contain CRLF on Windows. The test
            # writes a deliberate normalized UTF-8 artifact without altering
            # the original U01 execution observation.
            normalized_bytes=document["output"].replace("\r\n","\n").encode("utf-8")
            file_readback.write_bytes(normalized_bytes)
            if file_readback.read_bytes()!=normalized_bytes:
                raise RuntimeError("FILE_OPERATION_OUTPUT_NOT_READABLE")
            if document.get("connection_epoch") != connected["connection_epoch"]:
                raise RuntimeError("FILE_OPERATION_SESSION_CHANGED")
            exact_file_operation = ("transform_json" if name == "JQ_CLI_V1"
                                    else "read_yaml_json")
            if not next(x for x in connection.capabilities()["operations"]
                        if x["name"] == exact_file_operation)["verified"]:
                raise RuntimeError("FILE_OPERATION_CAPABILITY_NOT_VERIFIED")
            file_operation_pass=True
            # A selectable query can work directly on the user's existing
            # source without requiring an additional query/filter file.
            query_op = "query_json" if name == "JQ_CLI_V1" else "query_yaml"
            query_expression = (".rows | map(.units * .price) | add"
                                if name == "JQ_CLI_V1" else ".rows[0].price")
            query_expected = 59 if name == "JQ_CLI_V1" else 19
            invalid_query = connection.invoke(query_op, {"expression":query_expression})
            if (invalid_query["status"]!="CLI_INPUT_INVALID" or
                    invalid_query["executionOutcome"]!="NONE"):
                raise RuntimeError("FILE_QUERY_MISSING_INPUT_NOT_REJECTED")
            missing_source = connection.invoke(query_op,{
                "expression":query_expression,"input":str(root/"nonexistent-query-input")})
            if (missing_source["status"]!="EXECUTION_FAILED" or
                    missing_source["executionOutcome"]!="UNKNOWN"):
                raise RuntimeError("FILE_QUERY_MISSING_SOURCE_FALSE_SUCCESS")
            if next(x for x in connection.capabilities()["operations"]
                    if x["name"]==query_op)["verified"]:
                raise RuntimeError("FAILED_FILE_QUERY_FALSELY_VERIFIED")
            queried = connection.invoke(query_op,{
                "expression":query_expression,"input":str(source_path)})
            if (queried["status"]!="EXECUTED" or
                    queried["executionOutcome"]!="ACKNOWLEDGED" or
                    queried["output"].strip()!=str(query_expected) or
                    queried.get("connection_epoch")!=connected["connection_epoch"]):
                raise RuntimeError("FILE_QUERY_REAL_RESULT_FAILED")
            query_readback=root/("query-output-"+name+".txt")
            query_readback.write_bytes(queried["output"].replace("\r\n","\n").encode("utf-8"))
            if query_readback.read_bytes().decode("utf-8").strip()!=str(query_expected):
                raise RuntimeError("FILE_QUERY_READBACK_FAILED")
            if not next(x for x in connection.capabilities()["operations"]
                        if x["name"]==query_op)["verified"]:
                raise RuntimeError("FILE_QUERY_NOT_VERIFIED")
            # A real user document may exceed the legacy 128-KiB stdout cap.
            # The host must explicitly approve the larger bounded result.
            large_rows=[{"id":index,"region":"Москва","quantity":index%9,
                         "name":"Номенклатура склада № %04d" % index}
                        for index in range(4000)]
            large_source=root/("user large source " + name + (
                ".json" if name=="JQ_CLI_V1" else ".yaml"))
            if name=="JQ_CLI_V1":
                large_source.write_bytes(json.dumps({"rows":large_rows},
                    ensure_ascii=False).encode("utf-8"))
            else:
                large_source.write_bytes(("rows:\n"+"".join(
                    "  - id: %d\n    region: \"Москва\"\n    quantity: %d\n"
                    "    name: \"Номенклатура склада № %04d\"\n"
                    % (i, i%9, i) for i in range(4000))).encode("utf-8"))
            large_request={"expression":".","input":str(large_source)}
            old_limit={**manifest, "max_output_bytes":131072}
            old_connection=CliConnection(old_limit,str(program))
            if old_connection.connect()["status"]!="CONNECTED":
                raise RuntimeError("DEFAULT_LIMIT_PROBE_NOT_CONNECTED")
            old_out=old_connection.invoke(query_op,large_request)
            if (old_out["status"]!="CLI_OUTPUT_TOO_LARGE" or
                    old_out["executionOutcome"]!="UNKNOWN"):
                raise RuntimeError("DEFAULT_LIMIT_FALSELY_ACCEPTED_LARGE_FILE")
            old_connection.disconnect()
            large_out=connection.invoke(query_op,large_request)
            if (large_out["status"]!="EXECUTED" or
                    large_out["executionOutcome"]!="ACKNOWLEDGED" or
                    not 131072<len(large_out["output"].encode("utf-8"))<1048576):
                raise RuntimeError("APPROVED_LARGE_DOCUMENT_INVOCATION_FAILED")
            parsed=json.loads(large_out["output"])
            if (len(parsed.get("rows",[]))!=4000 or
                    parsed["rows"][-1]["name"]!=large_rows[-1]["name"]):
                raise RuntimeError("LARGE_DOCUMENT_CONTENT_MISMATCH")
            large_readback=root/("large document output "+name+".json")
            large_readback.write_bytes(large_out["output"].encode("utf-8"))
            if (hashlib.sha256(large_readback.read_bytes()).digest()!=
                    hashlib.sha256(large_out["output"].encode("utf-8")).digest()):
                raise RuntimeError("LARGE_DOCUMENT_READBACK_MISMATCH")
            large_document_bytes=large_readback.stat().st_size
            large_document_elapsed_ms=large_out["elapsed_ms"]
            if connection.invoke("not_declared")["status"]!="OPERATION_UNSUPPORTED":
                raise RuntimeError("UNDECLARED_OPERATION_ACCEPTED")
            connection.disconnect()
            if connection.invoke("evaluate",{"expression":"6 * 7"})["status"]!="NOT_CONNECTED":
                raise RuntimeError("DISCONNECTED_INVOKE_ACCEPTED")
            if connection.recover()["status"]!="CONNECTED":
                raise RuntimeError("RECONNECT_FAILED")
            connection.disconnect()
            shadow_dir=root/"identity-test"/name
            shadow_dir.mkdir(parents=True,exist_ok=True)
            shadow=shadow_dir/program.name
            shutil.copy2(program,shadow)
            shadow_connection=CliConnection(manifest,str(shadow))
            if shadow_connection.connect()["status"]!="CONNECTED":
                raise RuntimeError("SHADOW_CONNECT_FAILED")
            if shadow_connection.invoke("evaluate",{"expression":"6 * 7"})["status"]!="EXECUTED":
                raise RuntimeError("SHADOW_INVOKE_FAILED")
            st=shadow.stat()
            os.utime(shadow,ns=(st.st_atime_ns,st.st_mtime_ns+10_000_000_000))
            if shadow_connection.capabilities()["status"]!="CONNECTION_LOST":
                raise RuntimeError("SHADOW_CAPABILITY_IDENTITY_CHANGE_UNDETECTED")
            if any(x["verified"] for x in shadow_connection.capabilities()["operations"]):
                raise RuntimeError("SHADOW_STALE_CAPABILITY")
            if shadow_connection.verify()["status"]!="NOT_CONNECTED":
                raise RuntimeError("SHADOW_VERIFY_AFTER_DISCONNECT_FAILED")
            if shadow_connection.recover()["status"]!="CONNECTED":
                raise RuntimeError("SHADOW_RECOVER_FAILED")
            if any(x["verified"] for x in shadow_connection.capabilities()["operations"]):
                raise RuntimeError("SHADOW_FALSE_CAPABILITY_AFTER_RECOVERY")
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
                    capture_output=True,text=True,timeout=35,check=False)
                if rust.returncode != 0:
                    raise RuntimeError("RUST_HOST_EXIT_%s: %s" % (
                        rust.returncode, rust.stderr.strip()[:1200]))
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
                           "installed_binary_bytes":installed_binary_bytes,
                           "existing_install_reused":True,"reinstalled":False,
                           "real_file_operation":file_operation_pass,
                           "real_query_operation":query_op,
                           "real_query_readback":True,
                           "bounded_large_document_bytes":large_document_bytes,
                           "large_document_ms":large_document_elapsed_ms,
                           "default_large_output_rejected":True})
    print(json.dumps({"schema":"IWXA_PROGRAM_INTEGRATION_QUALIFICATION/1",
                      "status":"PASS","platform":platform,"results":report},sort_keys=True))

if __name__=="__main__":
    qualify()
