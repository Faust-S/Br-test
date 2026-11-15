"""Compare original and IWXA-patched headless workers on the same runner.

Both binaries are compiled from the pinned upstream commit and exercise the
same navigate + dump_text + shutdown wire commands. The unpatched original
has no native per-request origin gate; this difference is recorded explicitly.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from iwxa_obscura.worker import ObscuraWorkerController
from iwxa_obscura.controller import ObscuraControllerError

def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):h.update(chunk)
    return h.hexdigest()

def monitor(stop, samples, holder):
    try:
        import psutil
    except ImportError:return
    parent=psutil.Process(os.getpid())
    while not stop.wait(.02):
        rss=0
        cpu=0.
        for child in parent.children(recursive=True):
            try:
                p=child.memory_info()
                t=child.cpu_times()
                rss+=p.rss
                cpu+=(t.user+t.system)*1000
            except (psutil.NoSuchProcess, psutil.AccessDenied):continue
        samples.append(rss)
        holder.append(cpu)

def original(binary, url, timeout):
    env={k:os.environ[k] for k in ("PATH","SystemRoot","WINDIR","LANG","TZ") if k in os.environ}
    env["OBSCURA_OBEY_ROBOTS"]="1"
    with tempfile.TemporaryDirectory(prefix="iwxa-original-obscura-") as temp:
        env.update({"HOME":temp,"USERPROFILE":temp,"TEMP":temp,"TMP":temp})
        cmds=[{"cmd":"navigate","url":url,"waitUntil":"networkidle2"},
              {"cmd":"dump_text"},{"cmd":"shutdown"}]
        payload=("\n".join(json.dumps(x) for x in cmds)+"\n").encode()
        kw={"creationflags":subprocess.CREATE_NO_WINDOW} if os.name=="nt" else {"start_new_session":True}
        try:
            p=subprocess.Popen([str(binary)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE,env=env,cwd=temp,**kw)
            stdout,stderr=p.communicate(payload,timeout=timeout)
        except subprocess.TimeoutExpired:
            p.kill()
            p.communicate()
            raise RuntimeError("UPSTREAM_TIMEOUT")
        if p.returncode or len(stdout)>2_000_000:
            raise RuntimeError("UPSTREAM_PROCESS_FAILURE")
        try:
            lines=[json.loads(x) for x in stdout.decode().splitlines()]
            if len(lines)!=3 or any(x.get("ok") is not True for x in lines):
                raise ValueError()
            info=lines[0]["result"]
            if info.get("status") not in range(100,600) or not isinstance(lines[1]["result"],str):
                raise ValueError()
            return {"http_status":info["status"],"text_sha256":
                    hashlib.sha256(lines[1]["result"].encode()).hexdigest(),
                    "text_bytes":len(lines[1]["result"].encode())}
        except (ValueError,KeyError,TypeError):
            raise RuntimeError("UPSTREAM_PROTOCOL_INVALID") from None

def benchmark(baseline, patched, urls, runs, timeout):
    result={"schema":"IWXA_OBSCURA_NATIVE_COMPARISON/1.0","platform":sys.platform,
            "baseline_sha256":sha(baseline),"patched_sha256":sha(patched),
            "upstream_commit":"8534b401898ed17b62f69bdc3d5eb186bffb0d60",
            "same_runner":True,"headless_only":True,
            "caveat":"baseline lacks network origin gate; equal requested URL, unequal egress restrictions",
            "results":[]}
    try:
        import resource
    except ImportError:
        resource=None
    for url in urls:
        for i in range(runs):
            # Alternate baseline/patch order to reduce first-request warmup bias.
            ordered=(("upstream",baseline),("iwxa_patched",patched))
            if i % 2:
                ordered=tuple(reversed(ordered))
            for name,path in ordered:
                usage_before=resource.getrusage(resource.RUSAGE_CHILDREN) if resource else None
                stop=threading.Event()
                rss,cpu=[],[]
                sampler=threading.Thread(target=monitor,args=(stop,rss,cpu),daemon=True)
                began=time.monotonic()
                sampler.start()
                try:
                    if name=="upstream":
                        observation=original(path,url,timeout)
                    else:
                        origin="https://"+urlsplit(url).hostname
                        ctrl=ObscuraWorkerController(
                            binary=path,binary_sha256=sha(patched),
                            allowed_origins=frozenset({origin}),
                            timeout_seconds=timeout,max_output_bytes=1_500_000)
                        x=ctrl.observe(url)
                        observation={"http_status":x.http_status,
                                     "text_sha256":x.content_sha256,
                                     "text_bytes":x.content_bytes}
                    state="PASS"
                except (RuntimeError,ObscuraControllerError) as e:
                    state="FAIL"
                    observation={"error_code":getattr(e,"code",str(e))[:80]}
                finally:
                    stop.set()
                    sampler.join(timeout=1)
                usage_after=resource.getrusage(resource.RUSAGE_CHILDREN) if resource else None
                exact_cpu_ms=(
                    round((usage_after.ru_utime + usage_after.ru_stime
                           - usage_before.ru_utime - usage_before.ru_stime)*1000,3)
                    if usage_before is not None else None
                )
                result["results"].append({
                    "url":url,"variant":name,"iteration":i,"state":state,
                    "wall_ms":round((time.monotonic()-began)*1000),
                    "peak_sampled_rss_bytes":max(rss) if rss else None,
                    "peak_sampled_cpu_time_ms":max(cpu) if cpu else None,
                    "reaped_child_cpu_time_ms":exact_cpu_ms,
                    "cpu_quality":("OS cumulative reaped child CPU time" if resource else
                                   "20ms sampled CPU-time lower bound" if cpu else "unavailable"),
                    **observation})
    result["median_wall_ms_by_variant"]={
        name:statistics.median([x["wall_ms"] for x in result["results"]
                                if x["state"]=="PASS" and x["variant"]==name])
        if any(x["state"]=="PASS" and x["variant"]==name for x in result["results"]) else None
        for name in ("upstream","iwxa_patched")}
    result["status"]="PASS" if all(x["state"]=="PASS" for x in result["results"]) else "HOLD"
    return result

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--baseline",type=Path,required=True)
    ap.add_argument("--patched",type=Path,required=True)
    ap.add_argument("--url",action="append",required=True)
    ap.add_argument("--runs",type=int,default=2)
    ap.add_argument("--timeout",type=float,default=45)
    ap.add_argument("--report",type=Path,required=True)
    a=ap.parse_args()
    if not 1<=a.runs<=5 or not 1<=a.timeout<=60 or len(a.url)<2:
        ap.error("invalid benchmark limits")
    report=benchmark(a.baseline.resolve(),a.patched.resolve(),a.url,a.runs,a.timeout)
    a.report.parent.mkdir(parents=True,exist_ok=True)
    a.report.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({"status":report["status"],"median_wall_ms":report["median_wall_ms_by_variant"]}))
    return 0 if report["status"]=="PASS" else 2

if __name__=="__main__":
    raise SystemExit(main())
