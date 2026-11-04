
import assert from "node:assert/strict";
import {createProductHostPort} from "../host-bindings/product-hosts.mjs";
import {integrationObservation} from "../host-bindings/ui-levels.mjs";
import {CliConnection,SCHEMA} from "../host-bindings/program-connection.mjs";
import {createTauriProgramPort} from "../host-bindings/tauri-adapter.mjs";
import {readFileSync} from "node:fs";
const [catalogPath,programId,executable]=process.argv.slice(2);
const spec=JSON.parse(readFileSync(catalogPath,"utf8")).programs.find(x=>x.program_id===programId);
assert.ok(spec);
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
 const caps=await port.capabilities({});
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
console.log(JSON.stringify({schema:"IWXA_PRODUCT_HOST_QUALIFICATION/1",status:"PASS",
  hosts:["agent","admin-center","atlas"],uses_real_cli:true,
  product_gui_integrated:false,ui_max_verified:"I2"}));
