
import assert from "node:assert/strict";
import {createProductHostPort} from "../host-bindings/product-hosts.mjs";
import {integrationObservation} from "../host-bindings/ui-levels.mjs";
import {CliConnection,SCHEMA} from "../host-bindings/program-connection.mjs";
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
}
console.log(JSON.stringify({schema:"IWXA_PRODUCT_HOST_QUALIFICATION/1",status:"PASS",
  hosts:["agent","admin-center","atlas"],uses_real_cli:true,
  product_gui_integrated:false,ui_max_verified:"I2"}));
