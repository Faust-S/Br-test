"""Isolated offline consumer smoke for packed Node and installed Python U03.

Build from local source and install into clean hosts; never import source via cwd.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv

SDK = Path(__file__).resolve().parents[1]


def run(argv, *, cwd=None, env=None):
    result = subprocess.run(argv, cwd=cwd, env=env, text=True,
                            capture_output=True, check=False, timeout=150)
    if result.returncode:
        raise AssertionError(
            f"U03_PACKAGED_CONSUMER_FAILURE code={result.returncode}\n"
            f"stdout={result.stdout[-3500:]}\nstderr={result.stderr[-3500:]}")
    return result.stdout


NODE_CONSUMER = r"""
import assert from 'node:assert/strict';
import {assess} from '@iwxa/capability-sdk';
import {bindModuleToU01Host} from '@iwxa/capability-sdk/host-binding';
import {projectU01Capabilities} from '@iwxa/capability-sdk/u01-bridge';
import {inspectT2Contract} from '@iwxa/capability-sdk/t2-manifest';
const descriptor={schema:'IWXA_MODULE_DESCRIPTOR/1.0.0',id:'iwxa.packaged.node',
 version:'0.1.0',provides:[],requires:[{id:'iwxa.cli.evaluate',range:'=1.0.0'}],
 hostCompatibility:[{hostId:'iwxa.node.consumer',range:'=0.1.0'}]};
const host={hostId:'iwxa.node.consumer',version:'0.1.0'};
const epoch='packagedhostepoch0001';
const current={schema:'IWXA_PROGRAM_CONNECTION/1.0.0',program_id:'JQ_CLI_V1',
 transport:'cli',status:'DECLARED',connected:true,connection_epoch:epoch,
 operations:[{name:'evaluate',declared:true,verified:true}]};
const receipt={observationRef:'host:packaged:node',observation:{
 schema:current.schema,program_id:current.program_id,transport:'cli',
 status:'EXECUTED',executed:true,connection_epoch:epoch,operation:'evaluate'}};
const bindings={programId:'JQ_CLI_V1',bindings:[
 {operation:'evaluate',id:'iwxa.cli.evaluate',version:'1.0.0'}],receipts:{evaluate:receipt}};
assert.equal(assess(descriptor,host,projectU01Capabilities(current,bindings)).compatible,true);
let attachCalls=0,detachCalls=0;
const bridge=bindModuleToU01Host({descriptor,host,
 port:{capabilities:async()=>current},getBindings:()=>bindings,
 hooks:{attach:async()=>{attachCalls++;return {attached:true,bindingRef:'host:node:binding'};},
 detach:async ref=>{assert.equal(ref,'host:node:binding');detachCalls++;return {detached:true};}}});
assert.equal(bridge.describe().id,descriptor.id);
assert.equal((await bridge.assess()).compatible,true);
const attached=await bridge.attach();
assert.equal(attached.attached,true);
assert.equal((await bridge.detach(attached.hostReceipt.bindingRef)).detached,true);
assert.equal(attachCalls,1);assert.equal(detachCalls,1);
assert.equal(assess(descriptor,{...host,version:'0.2.0'},projectU01Capabilities(current,bindings)).compatible,false);
assert.equal(typeof inspectT2Contract,'function');
console.log(JSON.stringify({consumer:'node',packaged:true,status:'PASS'}));
"""

PY_CONSUMER = r"""
import json
from iwxa_capability_sdk import assess
from host_binding import U03HostBinding
from u01_bridge import observe_u01_capabilities
from t2_manifest import inspect_t2_contract
epoch='packagedhostepoch0001'
descriptor={'schema':'IWXA_MODULE_DESCRIPTOR/1.0.0','id':'iwxa.packaged.python',
 'version':'0.1.0','provides':[],
 'requires':[{'id':'iwxa.cli.evaluate','range':'=1.0.0'}],
 'hostCompatibility':[{'hostId':'iwxa.python.consumer','range':'=0.1.0'}]}
host={'hostId':'iwxa.python.consumer','version':'0.1.0'}
class Connection:
 def capabilities(self):
  return {'schema':'IWXA_PROGRAM_CONNECTION/1.0.0','program_id':'JQ_CLI_V1',
   'transport':'cli','status':'DECLARED','connected':True,'connection_epoch':epoch,
   'operations':[{'name':'evaluate','declared':True,'verified':True}]}
config={'programId':'JQ_CLI_V1','bindings':[
 {'operation':'evaluate','id':'iwxa.cli.evaluate','version':'1.0.0'}],
 'receipts':{'evaluate':{'observationRef':'host:packaged:python','observation':{
  'schema':'IWXA_PROGRAM_CONNECTION/1.0.0','program_id':'JQ_CLI_V1',
  'transport':'cli','status':'EXECUTED','executed':True,
  'connection_epoch':epoch,'operation':'evaluate'}}}}
conn=Connection()
assert assess(descriptor,host,observe_u01_capabilities(conn,config))['compatible']
attached=[]
detached=[]
binding=U03HostBinding(descriptor,host,conn,lambda:config,
 lambda *_:attached.append(1) or {'attached':True,'bindingRef':'host:python:binding'},
 lambda ref:detached.append(ref) or {'detached':True})
assert binding.describe()['id']==descriptor['id']
assert binding.assess()['compatible']
receipt=binding.attach()
assert receipt['attached']
assert binding.detach(receipt['hostReceipt']['bindingRef'])['detached']
assert len(attached)==1 and detached==['host:python:binding']
assert not assess(descriptor,{**host,'version':'0.2.0'},observe_u01_capabilities(conn,config))['compatible']
assert callable(inspect_t2_contract)
print(json.dumps({'consumer':'python','packaged':True,'status':'PASS'}))
"""


def main():
    with tempfile.TemporaryDirectory(prefix="iwxa-u03-dist-") as tmp:
        root = Path(tmp)
        node_host = root / "node-host"
        py_host = root / "py-host"
        node_host.mkdir()
        py_host.mkdir()
        run(["npm", "pack", str(SDK), "--pack-destination", str(root),
             "--silent"], cwd=node_host)
        archives = list(root.glob("*.tgz"))
        assert len(archives) == 1, "U03_NODE_ARCHIVE_MISSING"
        (node_host / "package.json").write_text(
            '{"name":"u03-independent-node-host","private":true,"type":"module"}',
            encoding="utf-8")
        run(["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund",
             "--offline", str(archives[0])], cwd=node_host)
        (node_host / "consumer.mjs").write_text(NODE_CONSUMER, encoding="utf-8")
        out_node = run(["node", "consumer.mjs"], cwd=node_host)
        assert json.loads(out_node.strip())["status"] == "PASS"
        venv_dir = root / "venv"
        venv.EnvBuilder(with_pip=True).create(venv_dir)
        interpreter = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        run([str(interpreter), "-m", "pip", "install", "--disable-pip-version-check",
             "--no-deps", str(SDK)], cwd=py_host)
        (py_host / "consumer.py").write_text(PY_CONSUMER, encoding="utf-8")
        clean_env = os.environ.copy()
        clean_env.pop("PYTHONPATH", None)
        out_py = run([str(interpreter), "-I", str(py_host / "consumer.py")],
                     cwd=py_host, env=clean_env)
        assert json.loads(out_py.strip())["status"] == "PASS"
        print(json.dumps({"schema": "IWXA_U03_PACKAGED_CONSUMERS/1",
                          "status": "PASS", "node": "PASS", "python": "PASS",
                          "outside_source_tree": True, "no_product_host_changes": True}))


if __name__ == "__main__":
    main()
