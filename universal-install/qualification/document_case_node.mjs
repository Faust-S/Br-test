// Real jq CLI through two existing product host ports. No task runtime or store.
import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {readFileSync,writeFileSync,existsSync} from "node:fs";
import {join,basename} from "node:path";
import {CliConnection} from "../host-bindings/program-connection.mjs";
import {createProductHostPort} from "../host-bindings/product-hosts.mjs";
const [mode,executable,dir,digest]=process.argv.slice(2);
if(!["build","verify"].includes(mode)||!dir)throw Error("DOCUMENT_CASE_INVOCATION_INVALID");
const hash=path=>createHash("sha256").update(readFileSync(path)).digest("hex");
const outputNames=["report.json","lines.csv","summary.md"];
const receiptPath=join(dir,"result-evidence.json");
if(mode==="verify"){
 if(!existsSync(receiptPath))throw Error("EVIDENCE_MISSING");
 const record=JSON.parse(readFileSync(receiptPath,"utf8"));
 assert.equal(record.schema,"IWXA_U01_DOCUMENT_CASE_EVIDENCE/1");
 assert.equal(record.status,"EXECUTED");
 assert.equal(record.programs.jq.digest,digest);
 for(const [name,value] of Object.entries(record.output_sha256)){
  if(!outputNames.includes(name)||!existsSync(join(dir,name))||
     hash(join(dir,name))!==value)throw Error("EVIDENCE_CHANGED:"+name);
 }
 assert.equal(Object.keys(record.output_sha256).length,outputNames.length);
 const report=JSON.parse(readFileSync(join(dir,"report.json"),"utf8"));
 assert.equal(report.schema,"IWXA_U01_DOCUMENT_REPORT/1");
 // No CLI program connection, re-install, or operation invocation in verify mode.
 console.log(JSON.stringify({schema:"IWXA_U01_DOCUMENT_CASE_READBACK/1",
  status:"PASS",verified_files:Object.keys(record.output_sha256).length,
  invoked_again:false,installed_again:false,order_count:report.order_count,
  gross_margin_kopeks:report.totals.margin_kopeks}));
 process.exit(0);
}
if(!executable||!/^[0-9a-f]{64}$/.test(digest))throw Error("DOCUMENT_CASE_EXECUTABLE_INVALID");
const spec={
 schema:"IWXA_CLI_PROGRAM/1",program_id:"JQ_CLI_V1",
 executable_name:basename(executable),executable_sha256:digest,
 version_args:["--version"],version_contains:"jq-1.8.1",
 operations:{
  build_report:["-n","--slurpfile","orders","{orders}","--slurpfile","catalog","{catalog}",
   "--slurpfile","returns","{returns}","--slurpfile","stock","{stock}","-f","{filter}"],
  render_file:["-r","-f","{filter}","{input}"],
  reject_invalid:["-n","{expression}"]
 }
};
function consumer(name){
 const connection=new CliConnection(spec,executable);
 const port=createProductHostPort(name,async({operation,request})=>
  operation==="invoke"?
   connection.invoke(request.operation,request.params):connection[operation]());
 return {port,connection};
}
const agent=consumer("agent");
const atlas=consumer("atlas");
const start=performance.now();
for(const {port} of [agent,atlas]){
 const inspected=await port.inspect({});
 assert.equal(inspected.status,"DISCOVERED");
 assert.equal(inspected.installation_verified,true);
 assert.equal(inspected.connected,false);
 const attached=await port.connect({});
 assert.equal(attached.status,"CONNECTED");
 assert.ok(attached.connection_epoch);
 assert.equal((await port.capabilities({})).status,"DECLARED");
 assert.ok((await port.capabilities({})).operations.every(x=>!x.verified));
}
const aEpoch=agent.connection.capabilities().connection_epoch;
const bEpoch=atlas.connection.capabilities().connection_epoch;
assert.notEqual(aEpoch,bEpoch);
const build=await agent.port.invoke({operation:"build_report",params:{
 orders:join(dir,"sales.json"),catalog:join(dir,"catalog.json"),
 returns:join(dir,"returns.json"),stock:join(dir,"stock.json"),
 filter:join(dir,"build_report.jq")}});
assert.equal(build.status,"EXECUTED",JSON.stringify(build));
assert.equal(build.executionOutcome,"ACKNOWLEDGED");
assert.equal(build.connection_epoch,aEpoch);
const report=JSON.parse(build.output);
assert.equal(report.schema,"IWXA_U01_DOCUMENT_REPORT/1");
assert.ok(report.order_count>=20 && report.line_count>=40);
const normalizeText=value=>value.replace(/\r\n/g,"\n");
writeFileSync(join(dir,"report.json"),normalizeText(build.output),"utf8");
assert.equal(readFileSync(join(dir,"report.json"),"utf8"),normalizeText(build.output));
const render=async(name,filter)=>{
 const response=await atlas.port.invoke({operation:"render_file",params:{
   filter:join(dir,filter),input:join(dir,"report.json")}});
 assert.equal(response.status,"EXECUTED",JSON.stringify(response));
 assert.equal(response.connection_epoch,bEpoch);
 assert.equal(response.executionOutcome,"ACKNOWLEDGED");
 assert.ok(response.output.length>30);
 writeFileSync(join(dir,name),normalizeText(response.output),"utf8");
 assert.equal(readFileSync(join(dir,name),"utf8"),normalizeText(response.output));
};
await render("lines.csv","render_lines.jq");
await render("summary.md","render_summary.jq");
assert.equal((await agent.port.invoke({operation:"unlisted",params:{}})).status,
 "OPERATION_UNSUPPORTED");
const invalid=await atlas.port.invoke({operation:"render_file",params:{invalid:"x"}});
assert.equal(invalid.status,"CLI_INPUT_INVALID");
assert.equal(invalid.executionOutcome,"NONE");
const rejected=await agent.port.invoke({operation:"reject_invalid",params:{
 expression:"error(\"expected test failure\")"}});
assert.equal(rejected.status,"EXECUTION_FAILED");
assert.equal(rejected.executionOutcome,"UNKNOWN");
assert.equal(agent.connection.capabilities().operations.find(x=>x.name==="build_report").verified,true);
assert.equal(atlas.connection.capabilities().operations.find(x=>x.name==="render_file").verified,true);
const outputSha=Object.fromEntries(outputNames.map(name=>[name,hash(join(dir,name))]));
const evidence={
 schema:"IWXA_U01_DOCUMENT_CASE_EVIDENCE/1",status:"EXECUTED",
 programs:{jq:{program_id:"JQ_CLI_V1",digest,transport:"cli"}},
 host_consumers:["agent","atlas"],operations:["build_report","render_file"],
 connection_epoch:{agent:aEpoch,atlas:bEpoch},
 source_sha256:Object.fromEntries(["sales.json","catalog.json","returns.json","stock.json"]
   .map(name=>[name,hash(join(dir,name))])),
 output_sha256:outputSha,
 elapsed_ms:Math.round((performance.now()-start)*1000)/1000,
 rss_bytes:process.memoryUsage().rss,
 invoked_once_for_report:true,
 // This is a CI-owned evidence file, not a durable Agent receipt.
 receipt_authority:"QUALIFICATION_ONLY"
};
writeFileSync(receiptPath,JSON.stringify(evidence,null,2)+"\n","utf8");
const oldEpoch=aEpoch;
assert.equal((await agent.port.disconnect({})).status,"DISCONNECTED");
assert.equal((await agent.port.invoke({operation:"build_report",params:{}})).status,"NOT_CONNECTED");
assert.equal((await agent.port.recover({})).status,"CONNECTED");
assert.notEqual(agent.connection.capabilities().connection_epoch,oldEpoch);
assert.ok(agent.connection.capabilities().operations.every(x=>!x.verified));
assert.equal((await atlas.port.disconnect({})).status,"DISCONNECTED");
console.log(JSON.stringify({schema:"IWXA_U01_DOCUMENT_CASE_JS/1",
 status:"PASS",real_jq:true,order_count:report.order_count,
 line_count:report.line_count,outputs:outputNames,
 output_sha256:outputSha,host_consumers:["agent","atlas"],
 execution_ms:evidence.elapsed_ms,rss_bytes:evidence.rss_bytes,
 invocation_reused_connection:true,source_readback:true,
 no_implicit_retry:true,epoch_rotated:true}));
