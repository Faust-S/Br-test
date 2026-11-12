
import assert from "node:assert/strict";
import {createProductHostPort} from "../host-bindings/product-hosts.mjs";
import {integrationObservation} from "../host-bindings/ui-levels.mjs";
import {CliConnection,SCHEMA,argumentParameterNames} from "../host-bindings/program-connection.mjs";
import {createTauriProgramPort} from "../host-bindings/tauri-adapter.mjs";
import {readFileSync,writeFileSync,mkdtempSync,rmSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
const [catalogPath,programId,executable]=process.argv.slice(2);
const spec=JSON.parse(readFileSync(catalogPath,"utf8")).programs.find(x=>x.program_id===programId);
assert.ok(spec);
const fileOperation=programId==="JQ_CLI_V1"?"transform_json":"read_yaml_json";
const expectedParams=programId==="JQ_CLI_V1"?["filter","input"]:["input"];
const queryOperation=programId==="JQ_CLI_V1"?"query_json":"query_yaml";
assert.deepEqual(argumentParameterNames(spec.operations[fileOperation]),expectedParams);
assert.deepEqual(argumentParameterNames(spec.operations[queryOperation]),["expression","input"]);
const root=mkdtempSync(join(tmpdir(),"iwxa-u01-ports-files-"));
const source=join(root,programId==="JQ_CLI_V1"?"customer orders.json":"customer orders.yaml");
const filter=join(root,"transform.jq");
if(programId==="JQ_CLI_V1"){
 writeFileSync(source,'{"rows":[{"units":2,"price":19},{"units":3,"price":7}]}',"utf8");
 writeFileSync(filter,".rows | map(.units * .price) | add\n","utf8");
}else{
 writeFileSync(source,"rows:\n  - units: 2\n    price: 19\n  - units: 3\n    price: 7\n","utf8");
}
try{
for(const host of ["agent","admin-center","atlas"]){
 const connection=new CliConnection(spec,executable);
 const execute=async({schema,host:target,operation,request})=>{
  assert.equal(schema,SCHEMA);assert.equal(target,host);
  return operation==="invoke"
    ?connection.invoke(request.operation,request.params)
    :connection[operation]();
 };
 const port=createProductHostPort(host,execute);
 const observed=await port.inspect({});
 assert.equal(observed.status,"DISCOVERED");
 assert.equal((await port.connect({})).status,"CONNECTED");
 const invoked=await port.invoke({operation:"evaluate",params:{expression:"9 * 9"}});
 assert.equal(invoked.output.trim(),"81");
 const doc=await port.invoke({operation:fileOperation,params:programId==="JQ_CLI_V1"?
   {filter,input:source}:{input:source}});
 assert.equal(doc.status,"EXECUTED");
 assert.equal(doc.connection_epoch,invoked.connection_epoch);
 if(programId==="JQ_CLI_V1")assert.equal(doc.output.trim(),"59");
 else assert.deepEqual(JSON.parse(doc.output),{rows:[
   {units:2,price:19},{units:3,price:7}]});
 const outputFile=join(root,host+"-"+programId+".txt");
 writeFileSync(outputFile,doc.output,"utf8");
 assert.equal(readFileSync(outputFile,"utf8"),doc.output);
 // An ordinary user query can operate directly on a selected document.
 assert.equal((await port.invoke({operation:queryOperation,
   params:{expression:".rows[0].price"}})).status,"CLI_INPUT_INVALID");
 assert.equal((await port.capabilities({})).operations
   .find(x=>x.name===queryOperation).verified,false);
 const queried=await port.invoke({operation:queryOperation,params:{
   expression:".rows[0].price",input:source}});
 assert.equal(queried.status,"EXECUTED");
 assert.equal(queried.executionOutcome,"ACKNOWLEDGED");
 assert.equal(queried.output.trim(),"19");
 assert.equal(queried.connection_epoch,invoked.connection_epoch);
 const queryReadback=join(root,host+"-"+programId+"-query.txt");
 writeFileSync(queryReadback,queried.output.replace(/\r\n/g,"\n"),"utf8");
 assert.equal(readFileSync(queryReadback,"utf8").trim(),"19");
 const caps=await port.capabilities({});
 assert.equal(caps.operations.find(x=>x.name===queryOperation).verified,true);
 assert.equal(caps.operations.find(x=>x.name===fileOperation).verified,true);
 const ui=integrationObservation({discovered:true,connected:true,capabilities:caps.operations});
 assert.equal(ui.highestVerified,"I2");
 assert.equal(ui.levels.find(x=>x.level==="I3").verified,false);
 assert.equal((await port.disconnect({})).status,"DISCONNECTED");
 const tauriConnection=new CliConnection(spec,executable);
 const nativeInvoke=async(command,{envelope})=>{
  assert.equal(command,"iwxa_program_connection");
  assert.equal(envelope.schema,SCHEMA);
  return envelope.operation==="invoke"
    ?tauriConnection.invoke(envelope.request.operation,envelope.request.params)
    :tauriConnection[envelope.operation]();
 };
 const tauri=createTauriProgramPort(nativeInvoke);
 assert.equal((await tauri.connect({})).status,"CONNECTED");
 assert.equal((await tauri.invoke({operation:"evaluate",params:{expression:"5 * 5"}})).output.trim(),"25");
 assert.equal((await tauri.disconnect({})).status,"DISCONNECTED");
}
}finally{rmSync(root,{recursive:true,force:true});}
console.log(JSON.stringify({schema:"IWXA_PRODUCT_HOST_QUALIFICATION/1",status:"PASS",
  hosts:["agent","admin-center","atlas"],uses_real_cli:true,
  real_file_operation:fileOperation,real_file_readback:true,
  real_query_operation:queryOperation,query_readback:true,
  declared_parameters:expectedParams,
  product_gui_integrated:false,ui_max_verified:"I2"}));
