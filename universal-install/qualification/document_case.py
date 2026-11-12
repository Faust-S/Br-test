"""End-to-end real-file U01 case: pinned yq + jq, Python and JS hosts.

Outputs are CI qualification artifacts, not Agent Task/Store/Result receipts.
Every transformation runs through the existing public U01 CLI connections.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "universal-install"))
from iwxa_universal_install import (
    ApprovedInstall, ApprovedRemoval, ManagedArtifactBackend, ManagedArtifactSpec
)
from iwxa_universal_install.managed import _native_platform
from iwxa_universal_install.program_connection import CliConnection

CATALOG = ROOT / "universal-install" / "catalog"
NODE_HOST = ROOT / "universal-install" / "qualification" / "document_case_node.mjs"
UPSTREAM = {"JQ_CLI_V1": "jqlang/jq", "YQ_CLI_V1": "mikefarah/yq"}

REPORT_JQ = r'''
($catalog[0].products | map({key:.sku,value:.}) | from_entries) as $products |
($returns[0].returns | map({key:(.order_id+"|"+.sku),value:.quantity}) | from_entries) as $refunds |
[$orders[0].orders[] as $order | $order.items[] as $line |
  ($products[$line.sku] // error("SKU_MISSING")) as $product |
  ($refunds[$order.id+"|"+$line.sku] // 0) as $returned |
  if $returned > $line.quantity or $returned < 0
  then error("RETURN_QUANTITY_INVALID")
  else
    {order_id:$order.id,date:$order.date,region:$order.region,sku:$line.sku,
     sold_qty:$line.quantity,returned_qty:$returned,
     net_qty:($line.quantity-$returned),
     revenue_kopeks:(($line.quantity-$returned)*$product.unit_price_kopeks),
     cost_kopeks:(($line.quantity-$returned)*$product.unit_cost_kopeks),
     margin_kopeks:(($line.quantity-$returned)*($product.unit_price_kopeks-$product.unit_cost_kopeks))}
  end
] as $rows |
{schema:"IWXA_U01_DOCUMENT_REPORT/1",
 order_count:($orders[0].orders|length),
 line_count:($rows|length),
 rows:$rows,
 totals:{
   sold_qty:($rows|map(.sold_qty)|add),
   returned_qty:($rows|map(.returned_qty)|add),
   net_qty:($rows|map(.net_qty)|add),
   revenue_kopeks:($rows|map(.revenue_kopeks)|add),
   cost_kopeks:($rows|map(.cost_kopeks)|add),
   margin_kopeks:($rows|map(.margin_kopeks)|add)
 },
 by_region:($rows|group_by(.region)|map({
   region:.[0].region,
   revenue_kopeks:(map(.revenue_kopeks)|add),
   cost_kopeks:(map(.cost_kopeks)|add),
   margin_kopeks:(map(.margin_kopeks)|add)
 })),
 stock_summary:([$stock[0].stock[] as $s |
   ($rows|map(select(.sku==$s.sku))|map(.net_qty)|add // 0) as $used |
   {sku:$s.sku,stock_before:$s.units,shipped_net:$used,stock_after:($s.units-$used)}
 ])
}
'''
CSV_JQ = r'''
(["order_id","date","region","sku","sold_qty","returned_qty","net_qty",
  "revenue_kopeks","cost_kopeks","margin_kopeks"],
 (.rows[] | [.order_id,.date,.region,.sku,.sold_qty,.returned_qty,.net_qty,
             .revenue_kopeks,.cost_kopeks,.margin_kopeks])) | @csv
'''
SUMMARY_JQ = r'''
"# Отчёт по отгрузкам и возвратам — октябрь 2026\n\n"
+ "Заказов: " + (.order_count|tostring) + "\n"
+ "Строк заказа: " + (.line_count|tostring) + "\n"
+ "Отгружено, шт.: " + (.totals.sold_qty|tostring) + "\n"
+ "Возвращено, шт.: " + (.totals.returned_qty|tostring) + "\n"
+ "Чистая выручка, коп.: " + (.totals.revenue_kopeks|tostring) + "\n"
+ "Себестоимость, коп.: " + (.totals.cost_kopeks|tostring) + "\n"
+ "Валовая маржа, коп.: " + (.totals.margin_kopeks|tostring) + "\n\n"
+ "## По регионам\n\n"
+ (.by_region | map("- " + .region + ": " + (.revenue_kopeks|tostring) + " коп.") | join("\n"))
+ "\n\n## Остатки после отгрузок и возвратов\n\n"
+ (.stock_summary | map("- " + .sku + ": " + (.stock_after|tostring) + " шт.") | join("\n"))
+ "\n"
'''

def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def write_json(path, value):
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))

def as_yaml(root, items):
    def value(v):
        return json.dumps(v, ensure_ascii=False) if isinstance(v, str) else str(v)
    lines = [root + ":"]
    for item in items:
        (first_key, first_value), *rest = list(item.items())
        lines.append(f"  - {first_key}: {value(first_value)}")
        for k, v in rest:
            lines.append(f"    {k}: {value(v)}")
    return "\n".join(lines) + "\n"

def make_sources(work):
    skus = [
        ("LED-18W", "Светильник LED 18 Вт", 189000, 108000, 130),
        ("WIRE-2M", "Кабель питания 2 м", 56000, 27000, 160),
        ("SWITCH-1", "Выключатель", 89000, 46000, 95),
        ("SENSOR-01", "Датчик движения", 235000, 128000, 70),
        ("PANEL-60", "Панель потолочная", 425000, 261000, 65),
        ("BOX-SET", "Монтажный комплект", 31000, 15000, 180),
    ]
    products = [dict(sku=s, title=t, unit_price_kopeks=p, unit_cost_kopeks=c)
                for s,t,p,c,_ in skus]
    stock = [dict(sku=s, units=n) for s,_,_,_,n in skus]
    regions = ("Москва", "Санкт-Петербург", "Казань", "Екатеринбург")
    orders = []
    for n in range(1, 25):
        first, second = skus[n % len(skus)][0], skus[(n+2) % len(skus)][0]
        orders.append({
            "id": f"ORD-2026-10-{n:04d}",
            "date": f"2026-10-{n:02d}",
            "region": regions[(n-1) % len(regions)],
            "items": [{"sku": first, "quantity": 1 + (n % 4)},
                      {"sku": second, "quantity": 2 + (n % 3)}]
        })
    returns = [{"order_id": orders[n-1]["id"],
                "sku": orders[n-1]["items"][0]["sku"], "quantity": 1}
               for n in (2, 7, 14, 19)]
    write_json(work / "sales.json", {"orders": orders})
    for name, root, values in (
        ("catalog.yaml", "products", products),
        ("returns.yaml", "returns", returns),
        ("stock.yaml", "stock", stock)):
        (work / name).write_bytes(as_yaml(root, values).encode("utf-8"))
    for name, value in (
        ("build_report.jq", REPORT_JQ), ("render_lines.jq", CSV_JQ),
        ("render_summary.jq", SUMMARY_JQ)):
        (work / name).write_bytes((value.strip()+"\n").encode("utf-8"))
    return {"orders": orders, "products": products, "returns": returns, "stock": stock}

def installed_program(name, filename, platform, root):
    catalog = json.loads((CATALOG / filename).read_text(encoding="utf-8"))
    selected = catalog["platforms"].get(platform)
    if selected is None:
        raise RuntimeError("UPSTREAM_PLATFORM_UNSUPPORTED:" + name + ":" + platform)
    pkg = ManagedArtifactSpec(
        program_id=name, slug=catalog["slug"], release_id=catalog["upstream_tag"],
        platform=platform, executable_name=selected["executable"],
        archive_name=selected["asset"], archive_format="binary",
        archive_sha256=selected["sha256"], executable_sha256=selected["sha256"],
        download_url="https://github.com/"+UPSTREAM[name]+"/releases/download/"+
            catalog["upstream_tag"]+"/"+selected["asset"]
    )
    backend = ManagedArtifactBackend()
    stage = str(root / "stage")
    destination = str(root / "installed")
    prepared = backend.prepare(pkg,stage)
    if prepared.status != "STAGED_NOT_ACTIVATED":
        raise RuntimeError("PROGRAM_NOT_PREPARED:" + name)
    approval = ApprovedInstall(name, pkg.fingerprint, "DOCUMENTS-USER-APPROVED",destination,True)
    applied = backend.apply(pkg,stage,approval)
    if not applied.installed:
        raise RuntimeError("PROGRAM_INSTALL_FAILED:" + name)
    executable = Path(applied.executable)
    identity = (executable.stat().st_size, executable.stat().st_mtime_ns)
    again = backend.apply(pkg,stage,approval)
    if not again.installed or again.executable != applied.executable:
        raise RuntimeError("INSTALL_NOT_IDEMPOTENT:" + name)
    if (executable.stat().st_size,executable.stat().st_mtime_ns) != identity:
        raise RuntimeError("INSTALL_REPLACED_EXISTING_PROGRAM:" + name)
    if not backend.recover(pkg,destination).installed:
        raise RuntimeError("INSTALL_READBACK_FAILED:" + name)
    return pkg,backend,executable,selected["sha256"]

def check_financials(actual, source):
    catalog = {x["sku"]:x for x in source["products"]}
    returns = {(x["order_id"],x["sku"]):x["quantity"] for x in source["returns"]}
    expected = {"sold_qty":0,"returned_qty":0,"net_qty":0,
                "revenue_kopeks":0,"cost_kopeks":0,"margin_kopeks":0}
    regions = {}
    qty_by_sku = {}
    for order in source["orders"]:
        for line in order["items"]:
            item = catalog[line["sku"]]
            sold = line["quantity"]
            refunded = returns.get((order["id"],line["sku"]),0)
            net = sold-refunded
            rev = net*item["unit_price_kopeks"]
            cost = net*item["unit_cost_kopeks"]
            margin = rev-cost
            fields = {"sold_qty":sold,"returned_qty":refunded,"net_qty":net,
                      "revenue_kopeks":rev,"cost_kopeks":cost,"margin_kopeks":margin}
            for key in expected:
                expected[key] += fields[key]
            r = regions.setdefault(order["region"],{"revenue_kopeks":0,
                "cost_kopeks":0,"margin_kopeks":0})
            for field in r:
                r[field] += fields[field]
            qty_by_sku[line["sku"]] = qty_by_sku.get(line["sku"],0)+net
    if actual["totals"] != expected or actual["order_count"] != len(source["orders"]):
        raise RuntimeError("REPORT_FINANCIAL_MISMATCH")
    if actual["line_count"] != sum(len(x["items"]) for x in source["orders"]):
        raise RuntimeError("REPORT_LINE_COUNT_MISMATCH")
    if len(actual["rows"]) != actual["line_count"]:
        raise RuntimeError("REPORT_DETAIL_LOST")
    for row in actual["by_region"]:
        if row["region"] not in regions:
            raise RuntimeError("REGION_UNKNOWN")
        if {key:row[key] for key in regions[row["region"]]} != regions[row["region"]]:
            raise RuntimeError("REGION_FINANCIAL_MISMATCH")
    if len(actual["by_region"]) != len(regions):
        raise RuntimeError("REGION_MISSING")
    for item in actual["stock_summary"]:
        stock_before = next(x["units"] for x in source["stock"] if x["sku"]==item["sku"])
        if item["stock_after"] != stock_before-qty_by_sku.get(item["sku"],0):
            raise RuntimeError("STOCK_MISMATCH")
        if item["stock_after"] < 0:
            raise RuntimeError("NEGATIVE_STOCK")
    return expected

def call_node(mode, executable, root, digest, *, check=True):
    cmd = ["node", str(NODE_HOST), mode, str(executable), str(root), digest]
    started = time.perf_counter()
    response = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=35, check=False)
    wall = round((time.perf_counter()-started)*1000,3)
    if check and response.returncode:
        raise RuntimeError("NODE_HOST_ERROR:"+response.stderr[-1500:])
    return response,wall

def run_case(destination):
    platform = _native_platform()
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="iwxa-documents-case-") as directory:
        root = Path(directory).resolve()
        work = root / "documents"
        work.mkdir()
        user_notes = root / "installed" / "owner-notes.txt"
        user_notes.parent.mkdir()
        user_notes.write_text("preserve existing user file\n",encoding="utf-8")
        source = make_sources(work)
        missing_spec = {"schema":"IWXA_CLI_PROGRAM/1","program_id":"MISSING_TOOL",
                        "executable_name":"not-installed-cli",
                        "version_args":["--version"],"version_contains":"1.0",
                        "operations":{"read":["--help"]}}
        missing = CliConnection(missing_spec, str(root / "not-installed"))
        if missing.inspect()["status"]!="NOT_FOUND" or missing.connect()["status"]!="NOT_FOUND":
            raise RuntimeError("PROGRAM_MISSING_NOT_REPORTED")
        acquired = []
        try:
            for program, filename in (("YQ_CLI_V1","yq-4.54.1.json"),
                                      ("JQ_CLI_V1","jq-1.8.1.json")):
                pkg,backend,executable,digest=installed_program(program,filename,platform,root)
                acquired.append((pkg,backend,executable,digest))
            by_name={item[0].program_id:item for item in acquired}
            _,_,yq_executable,yq_digest=by_name["YQ_CLI_V1"]
            _,_,jq_executable,jq_digest=by_name["JQ_CLI_V1"]
            spec={"schema":"IWXA_CLI_PROGRAM/1","program_id":"YQ_CLI_V1",
                  "executable_name":yq_executable.name,"executable_sha256":yq_digest,
                  "version_args":["--version"],"version_contains":"v4.54.1",
                  "operations":{"normalize":["eval","-o=json",".","{path}"]}}
            yq=CliConnection(spec,str(yq_executable))
            before=yq.inspect()
            if before["status"]!="DISCOVERED" or not before["installation_verified"]:
                raise RuntimeError("YQ_INSTALLATION_IDENTITY_UNVERIFIED")
            attached=yq.connect()
            if attached["status"]!="CONNECTED" or not attached.get("connection_epoch"):
                raise RuntimeError("YQ_CONNECTION_FAILED")
            if yq.capabilities()["operations"][0]["verified"]:
                raise RuntimeError("YQ_OPERATION_VERIFIED_BEFORE_EXECUTION")
            if yq.invoke("missing")["status"]!="OPERATION_UNSUPPORTED":
                raise RuntimeError("YQ_UNSUPPORTED_OPERATION_ACCEPTED")
            bad=yq.invoke("normalize",{"invalid":"x"})
            if bad["status"]!="CLI_INPUT_INVALID" or bad["executionOutcome"]!="NONE":
                raise RuntimeError("YQ_INVALID_ARGUMENT_ACCEPTED")
            timings=[]
            for name,expected_key in (("catalog","products"),("returns","returns"),
                                      ("stock","stock")):
                result=yq.invoke("normalize",{"path":str(work/(name+".yaml"))})
                if result["status"]!="EXECUTED" or result["executionOutcome"]!="ACKNOWLEDGED":
                    raise RuntimeError("YQ_REAL_TRANSFORM_FAILED:"+name)
                if result["connection_epoch"]!=attached["connection_epoch"]:
                    raise RuntimeError("YQ_SESSION_CHANGED")
                converted=json.loads(result["output"])
                if converted[expected_key]!=source[expected_key]:
                    raise RuntimeError("YQ_DATA_CHANGED:"+name)
                target=work/(name+".json")
                target.write_bytes(result["output"].replace("\r\n","\n").encode("utf-8"))
                if json.loads(target.read_text(encoding="utf-8"))!=converted:
                    raise RuntimeError("YQ_READBACK_FAILED:"+name)
                timings.append(result["elapsed_ms"])
            bad_file=work/"invalid.yaml"
            bad_file.write_bytes(b"items: [broken\n")
            rejected=yq.invoke("normalize",{"path":str(bad_file)})
            if rejected["status"]!="EXECUTION_FAILED" or rejected["executionOutcome"]!="UNKNOWN":
                raise RuntimeError("YQ_BAD_YAML_NOT_REJECTED")
            invalid_operation_epoch=attached["connection_epoch"]
            yq.disconnect()
            if yq.invoke("normalize",{"path":str(work/"stock.yaml")})["status"]!="NOT_CONNECTED":
                raise RuntimeError("YQ_DISCONNECTED_EXECUTED")
            reconnected=yq.recover()
            if reconnected["status"]!="CONNECTED" or reconnected["connection_epoch"]==invalid_operation_epoch:
                raise RuntimeError("YQ_RECONNECT_EPOCH_NOT_ROTATED")
            if yq.capabilities()["operations"][0]["verified"]:
                raise RuntimeError("YQ_STALE_VERIFIED_CAPABILITY")
            yq.disconnect()
            result, node_ms=call_node("build",jq_executable,work,jq_digest)
            js=json.loads(result.stdout.strip())
            if js["status"]!="PASS":
                raise RuntimeError("JS_CASE_NOT_PASSED")
            actual=json.loads((work/"report.json").read_text(encoding="utf-8"))
            financials=check_financials(actual,source)
            if len((work/"lines.csv").read_text(encoding="utf-8").splitlines())!=actual["line_count"]+1:
                raise RuntimeError("CSV_LINE_COUNT_MISMATCH")
            if "Валовая маржа" not in (work/"summary.md").read_text(encoding="utf-8"):
                raise RuntimeError("SUMMARY_MISSING_VALUES")
            proof=work/"result-evidence.json"
            original=proof.read_bytes()
            proof.unlink()
            missing_ack,_=call_node("verify",jq_executable,work,jq_digest,check=False)
            if missing_ack.returncode==0 or "EVIDENCE_MISSING" not in missing_ack.stderr:
                raise RuntimeError("LOST_ACK_ACCIDENTAL_REEXECUTION")
            proof.write_bytes(original)
            csv=work/"lines.csv"
            valid=csv.read_bytes()
            csv.write_bytes(valid+b"#modified\n")
            corrupt,_=call_node("verify",jq_executable,work,jq_digest,check=False)
            if corrupt.returncode==0 or "EVIDENCE_CHANGED" not in corrupt.stderr:
                raise RuntimeError("TAMPERED_READBACK_ACCEPTED")
            csv.write_bytes(valid)
            normalized_catalog=work/"catalog.json"
            original_catalog=normalized_catalog.read_bytes()
            normalized_catalog.write_bytes(original_catalog+b" ")
            stale_source,_=call_node("verify",jq_executable,work,jq_digest,check=False)
            if stale_source.returncode==0 or "EVIDENCE_SOURCE_CHANGED" not in stale_source.stderr:
                raise RuntimeError("STALE_SOURCE_READBACK_ACCEPTED")
            normalized_catalog.write_bytes(original_catalog)
            verified, readback_ms=call_node("verify",jq_executable,work,jq_digest)
            checked=json.loads(verified.stdout.strip())
            if (checked["status"]!="PASS" or checked["invoked_again"] is not False
                    or checked["verified_sources"]!=4):
                raise RuntimeError("HOST_RESTART_READBACK_FAILED")
            output_files=("sales.json","catalog.yaml","returns.yaml","stock.yaml",
                "catalog.json","returns.json","stock.json","report.json","lines.csv",
                "summary.md","result-evidence.json")
            for file in output_files:
                shutil.copy2(work/file,destination/file)
            available_mem=None
            if os.name!="nt":
                import resource
                raw=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                available_mem=int(raw*(1 if sys.platform=="darwin" else 1024))
            report={"schema":"IWXA_U01_USER_DOCUMENT_CASE/1","status":"PASS",
                "platform":platform,"real_programs":["jq 1.8.1","yq 4.54.1"],
                "input_files":4,"generated_files":6,
                "order_count":actual["order_count"],"line_count":actual["line_count"],
                "financials":financials,
                "host_consumers":["python:yq","node:agent:jq","node:atlas:jq"],
                "installed_existing_reused":True,
                "independent_financial_readback":True,
                "lost_ack_no_reexecution":True,
                "corrupted_file_detected":True,
                "source_change_detected":True,
                "host_restart_readback_no_invoke":True,
                "api_handshake_separate_qualification":True,
                "latency_ms":{"yq_operations":timings,"node_case_process":node_ms,
                              "node_readback_process":readback_ms,
                              "total_case_wall":round((time.perf_counter()-started)*1000,3)},
                "resources":{"python_peak_rss_bytes":available_mem,
                             "node_rss_bytes":js["rss_bytes"]},
                "source_sha256":{name:sha(work/name) for name in
                    ("sales.json","catalog.yaml","returns.yaml","stock.yaml")},
                "output_sha256":js["output_sha256"],
                "no_daemon":True,
                "agent_installed_e2e":False}
            write_json(destination/"case-result.json",report)
            print(json.dumps(report,ensure_ascii=True,sort_keys=True))
        finally:
            for pkg,backend,_,_ in reversed(acquired):
                approved=ApprovedRemoval(pkg.program_id,pkg.fingerprint,
                                        "DOCUMENTS-USER-APPROVED",str(root/"installed"),True)
                removed=backend.remove(pkg,approved)
                if removed.status!="REMOVED":
                    raise RuntimeError("PROGRAM_REMOVAL_FAILED:"+pkg.program_id)
            if user_notes.read_text(encoding="utf-8")!="preserve existing user file\n":
                raise RuntimeError("USER_FILE_DAMAGED")

if __name__=="__main__":
    if len(sys.argv)!=2:
        raise SystemExit("usage: document_case.py ARTIFACT_DIRECTORY")
    dest=Path(sys.argv[1]).resolve()
    dest.mkdir(parents=True,exist_ok=True)
    run_case(dest)
