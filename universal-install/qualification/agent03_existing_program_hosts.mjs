// Genuine Node executable, two independent U01 consumers, no new daemon.
import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {readFileSync} from "node:fs";
import {basename} from "node:path";
import {CliConnection} from "../host-bindings/program-connection.mjs";
import {createProductHostPort} from "../host-bindings/product-hosts.mjs";

const executable=process.execPath;
const digest=createHash("sha256").update(readFileSync(executable)).digest("hex");
const id="NODE_EXISTING_APP_V1";
const spec={
 schema:"IWXA_CLI_PROGRAM/1",program_id:id,
 executable_name:basename(executable),
 executable_sha256:digest,requires_running:true,
 version_args:["--version"],version_contains:process.version,
 operations:{
  echo:["-e","console.log(42)"],
  value:["-e","console.log(process.argv[1])","file={note}"],
  fail:["-e","process.exit(3)"],
  slow:["-e","setTimeout(()=>{},9000)"],
  binary:["-e","process.stdout.write(Buffer.from([255]))"],
  dynamic:["-e","{script}"]
 }
};
const observed={program_id:id,verified:true,status:"RUNNING",spawned:false,
 pid:process.pid,processStamp:"node-host-owned-stamp-one"};
const probe=()=>({...observed});
const make=()=>new CliConnection(spec,executable,{processProbe:probe});
const first=make(),second=make();
assert.equal(first.inspect().status,"DISCOVERED");
assert.equal(first.inspect().installation_identity.sha256,digest);
assert.equal(first.inspect().running,true);
const a=first.connect(),b=second.connect();
assert.equal(a.status,"CONNECTED");
assert.equal(b.status,"CONNECTED");
assert.notEqual(a.connection_epoch,b.connection_epoch);
assert.equal(a.installation_verified,true);
assert.equal(first.capabilities().operations[0].verified,false);
assert.equal(first.invoke("echo").output.trim(),"42");
assert.equal(second.invoke("value",{note:"a b.md"}).output.trim(),"file=a b.md");
assert.equal(first.invoke("fail").executionOutcome,"UNKNOWN");
assert.equal(first.invoke("missing").executionOutcome,"NONE");
assert.equal(first.invoke("echo",{bad:"input"}).executionOutcome,"NONE");
assert.equal(first.capabilities().operations.find(x=>x.name==="echo").verified,true);
const preCancelled=new AbortController();
preCancelled.abort();
const before=await first.invokeAsync("slow",{}, {signal:preCancelled.signal});
assert.equal(before.status,"CLI_CANCELLED");
assert.equal(before.executionOutcome,"NONE");
const interrupt=new AbortController();
const timer=setTimeout(()=>interrupt.abort(),160);
const start=performance.now();
const stopped=await first.invokeAsync("slow",{}, {signal:interrupt.signal});
clearTimeout(timer);
assert.equal(stopped.status,"CLI_CANCELLED");
assert.equal(stopped.executionOutcome,"UNKNOWN");
assert.ok(performance.now()-start<3500,"cancel failed to terminate child promptly");
assert.equal(first.capabilities().operations.find(x=>x.name==="slow").verified,false);
const asyncCompleted=await first.invokeAsync("echo");
assert.equal(asyncCompleted.status,"EXECUTED");
assert.equal(asyncCompleted.output.trim(),"42");
assert.equal(asyncCompleted.connection_epoch,a.connection_epoch);
const stalled=await first.invokeAsync("slow",{}, {timeoutMs:180});
assert.equal(stalled.status,"CLI_TIMED_OUT");
assert.equal(stalled.executionOutcome,"UNKNOWN");
const invalidTimeout=await first.invokeAsync("slow",{}, {timeoutMs:0});
assert.equal(invalidTimeout.status,"CLI_INPUT_INVALID");
assert.equal(invalidTimeout.executionOutcome,"NONE");
const undecodable=first.invoke("binary");
assert.equal(undecodable.status,"CLI_OUTPUT_ENCODING_UNSUPPORTED");
assert.equal(undecodable.executionOutcome,"UNKNOWN");
assert.equal(first.capabilities().operations.find(x=>x.name==="binary").verified,false);
const evaluated=first.invoke("dynamic",{script:"console.log(42)"});
assert.equal(evaluated.status,"EXECUTED");
assert.equal(first.capabilities().operations.find(x=>x.name==="dynamic").verified,true);
const failedDynamic=first.invoke("dynamic",{script:"process.exit(3)"});
assert.equal(failedDynamic.status,"EXECUTION_FAILED");
assert.equal(failedDynamic.executionOutcome,"UNKNOWN");
assert.equal(first.capabilities().operations.find(x=>x.name==="dynamic").verified,false);
assert.equal(first.verify().connection_epoch,a.connection_epoch);
const port=createProductHostPort("atlas",async({operation,request})=>
 operation==="invoke"?second.invoke(request.operation,request.params):second[operation]());
assert.equal((await port.capabilities({})).connected,true);
const viaHost=await port.invoke({operation:"echo",params:{}});
assert.equal(viaHost.status,"EXECUTED");
assert.equal(viaHost.output.trim(),"42");
assert.equal(viaHost.connection_epoch,b.connection_epoch);
observed.processStamp="node-host-owned-stamp-two";
const stale=second.capabilities();
assert.equal(stale.status,"PROCESS_CHANGED");
assert.equal(stale.connected,false);
assert.ok(stale.operations.every(x=>x.verified===false));
assert.equal(second.invoke("echo").executionOutcome,"NONE");
const resumed=second.recover();
assert.equal(resumed.status,"CONNECTED");
assert.notEqual(resumed.connection_epoch,b.connection_epoch);
observed.status="NOT_RUNNING";
assert.equal(first.capabilities().status,"PROCESS_NOT_RUNNING");
assert.equal(second.invoke("echo").status,"PROCESS_NOT_RUNNING");
assert.equal(second.invoke("echo").executionOutcome,"NONE");
observed.status="RUNNING";
assert.equal(second.connect().status,"CONNECTED");
second.disconnect();
assert.equal(second.capabilities().connection_epoch,undefined);
const absent=make();
absent.processProbe=null;
assert.equal(absent.connect().status,"RUNNING_PROCESS_UNVERIFIED");
assert.equal(absent.invoke("echo").executionOutcome,"NONE");
const unpinned=make();
unpinned.spec={...unpinned.spec,executable_sha256:"0".repeat(64)};
assert.equal(unpinned.connect().status,"INSTALLATION_MISMATCH");
assert.equal(unpinned.invoke("echo").executionOutcome,"NONE");
console.log(JSON.stringify({
 schema:"IWXA_U01_AGENT03_HOST_COMPATIBILITY/1",
 status:"PASS",real_executable:true,program_id:id,
 independent_host_consumers:2,real_output:"42",
 installed_identity_verified:true,process_restart_invalidates_session:true,
 no_hidden_program_launch:true,preflight_effect:"NONE",
 attempted_effect:"UNKNOWN",connected_epoch_isolation:true,
 cancel_before_start:"NONE",cancel_after_start:"UNKNOWN",async_readback:"42",
 native_timeout:"UNKNOWN",binary_output_refused:true
}));
