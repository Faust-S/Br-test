"""Source-only cold-host and idle overhead observation, never a release PASS claim."""
from __future__ import annotations
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
PY_IMPORT="import sys;sys.path.insert(0,'universal-install');import iwxa_universal_install.program_connection"
NODE_IMPORT="import('./universal-install/host-bindings/program-connection.mjs')"

def sample(command, times=5):
    values=[]
    for _ in range(times):
        start=time.perf_counter()
        result=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                              text=True,timeout=20,check=False,cwd=ROOT)
        elapsed=(time.perf_counter()-start)*1000
        if result.returncode:
            raise RuntimeError("HOST_COLD_SAMPLE_FAILED")
        values.append(elapsed)
    return round(statistics.median(values),3)

def qualify():
    python_base=sample([sys.executable,"-c","pass"])
    python_import=sample([sys.executable,"-c",PY_IMPORT])
    node_base=sample(["node","-e","void(0)"])
    node_import=sample(["node","-e",NODE_IMPORT])
    before=time.process_time()
    time.sleep(0.2)
    idle_cpu_ms=(time.process_time()-before)*1000
    memory={"python_rss_bytes":None,"node_rss_bytes":None}
    if os.name!="nt":
        import resource
        rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        memory["python_rss_bytes"]=int(rss*(1 if sys.platform=="darwin" else 1024))
    measured=subprocess.run(["node","-e","console.log(process.memoryUsage().rss)"],
                           capture_output=True,text=True,check=True,timeout=20)
    memory["node_rss_bytes"]=int(measured.stdout.strip())
    result={"schema":"IWXA_PROGRAM_HOST_PERFORMANCE/1","status":"MEASURED_UNQUALIFIED",
            "platform":sys.platform,"python_cold_base_ms":python_base,
            "python_cold_import_ms":python_import,
            "node_cold_base_ms":node_base,"node_cold_import_ms":node_import,
            "idle_python_cpu_ms_over_200ms":round(idle_cpu_ms,3),
            "memory":memory,"samples_per_cold_route":5,
            "product_host_measured":False,
            "not_a_5_percent_product_gate":True}
    print(json.dumps(result,sort_keys=True))

if __name__=="__main__":
    qualify()
