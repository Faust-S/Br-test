import {createHash} from "node:crypto";
import {readFileSync,copyFileSync,chmodSync,statSync,utimesSync,mkdtempSync,rmSync} from "node:fs";
import {tmpdir} from "node:os";
import {join,basename} from "node:path";
import {CliConnection,SCHEMA} from "../host-bindings/program-connection.mjs";
import {createHostAdapter} from "../host-bindings/host-port.mjs";

const [catalogPath,programId,executable]=process.argv.slice(2);
const catalog=JSON.parse(readFileSync(catalogPath,"utf8"));
const declared=catalog.programs.find(p=>p.program_id===programId);
const spec=declared?{...declared,executable_sha256:createHash("sha256").update(readFileSync(executable)).digest("hex")}:null;
if(!spec || !executable)throw Error("PROGRAM_HOST_ARGUMENT_INVALID");
const start=performance.now();
const shorter={...spec,version_contains:spec.version_contains.replace(/\.\d+$/,"")};
if(new CliConnection(shorter,executable).connect().status!=="VERSION_UNVERIFIED")
  throw Error("NODE_VERSION_PREFIX_FALSE_POSITIVE");
const connection=new CliConnection(spec,executable);
const discovery=connection.inspect();
if(discovery.status!=="DISCOVERED")throw Error("NODE_DISCOVERY_NOT_FOUND");
if(discovery.installation_verified!==true ||
   discovery.installation_identity.sha256!==spec.executable_sha256 ||
   discovery.running!==null||discovery.process!=="UNKNOWN")
 throw Error("NODE_INSTALLATION_IDENTITY_UNVERIFIED");
const connected=connection.connect();
if(connected.status!=="CONNECTED")throw Error("NODE_CONNECT_FAILED");
if(!connected.connection_epoch||connected.installation_verified!==true)
 throw Error("NODE_CONNECTED_IDENTITY_UNVERIFIED");
const ready=connection.capabilities();
if(ready.operations.length!==1 || ready.operations[0].verified!==false)throw Error("NODE_CAPABILITY_FALSE_CLAIM");
const executed=connection.invoke("evaluate",{expression:"6 * 7"});
if(executed.status!=="EXECUTED" || executed.output.trim()!=="42")throw Error("NODE_INVOKE_FAILED");
if(executed.connection_epoch!==connected.connection_epoch||
   executed.installation_identity?.sha256!==spec.executable_sha256)
 throw Error("NODE_EXECUTION_SESSION_OR_PIN_MISMATCH");
if(connection.invoke("evaluate",{expression:"7 * 8"}).output.trim()!=="56")throw Error("NODE_DYNAMIC_INPUT_FAILED");
if(!connection.capabilities().operations[0].verified)throw Error("NODE_CAPABILITY_NOT_VERIFIED");
if(connection.invoke("unlisted").status!=="OPERATION_UNSUPPORTED")throw Error("NODE_UNLISTED_OPERATION");
for(const inherited of ["constructor","toString","__proto__","valueOf"]){
 if(connection.invoke(inherited).status!=="OPERATION_UNSUPPORTED")
  throw Error("NODE_INHERITED_OPERATION_ACCEPTED_"+inherited);
}
if(connection.disconnect().status!=="DISCONNECTED")throw Error("NODE_DISCONNECT_FAILED");
if(connection.invoke("evaluate",{expression:"6 * 7"}).status!=="NOT_CONNECTED")throw Error("NODE_DISCONNECTED_EXECUTION");
if(connection.recover().status!=="CONNECTED")throw Error("NODE_RECOVER_FAILED");
if(connection.capabilities().connection_epoch===connected.connection_epoch)
 throw Error("NODE_DISCONNECT_DID_NOT_ROTATE_SESSION");
if(connection.verify().status!=="CONNECTED")throw Error("NODE_VERIFY_FAILED");
const temp=mkdtempSync(join(tmpdir(),"iwxa-identity-"));
try {
 const copy=join(temp,basename(executable));
 copyFileSync(executable,copy);
 chmodSync(copy,statSync(executable).mode);
 const test=new CliConnection(spec,copy);
 if(test.connect().status!=="CONNECTED")throw Error("NODE_IDENTITY_COPY_CONNECT");
 if(test.invoke("evaluate",{expression:"6 * 7"}).status!=="EXECUTED")
  throw Error("NODE_IDENTITY_COPY_INVOKE");
 const st=statSync(copy);
 utimesSync(copy,st.atime,new Date(st.mtimeMs+10_000));
 if(test.capabilities().status!=="CONNECTION_LOST")
  throw Error("NODE_IDENTITY_CHANGE_NOT_DETECTED_ON_CAPABILITIES");
 if(test.capabilities().operations[0].verified)
  throw Error("NODE_IDENTITY_STALE_CAPABILITY");
 if(test.verify().status!=="NOT_CONNECTED")
  throw Error("NODE_IDENTITY_VERIFY_AFTER_DISCONNECT");
 if(test.recover().status!=="CONNECTED")
  throw Error("NODE_IDENTITY_RECOVER_FAILED");
 if(test.capabilities().operations[0].verified)
  throw Error("NODE_RECOVER_FALSE_CAPABILITY");
} finally {rmSync(temp,{recursive:true,force:true});}
const uiHost=createHostAdapter(async ({operation,request})=>
  operation==="invoke" ? connection.invoke(request.operation,request.params) :
  connection[operation]());
const bridged=await uiHost.invoke({operation:"evaluate",params:{expression:"8 * 8"}});
if(bridged.status!=="EXECUTED"||bridged.output.trim()!=="64")
  throw Error("BROWSER_NEUTRAL_HOST_PORT_EXECUTION_FAILED");
connection.disconnect();
const result={schema:SCHEMA,host:"node",program_id:programId,status:"PASS",
  executed:true,output:executed.output.trim(),cold_ms:Math.round((performance.now()-start)*1000)/1000,
  connector_import_without_python:true,connected_again:true,
  installation_sha256_verified:true,session_scoped_execution:true,
  running_process_unclaimed:true};
console.log(JSON.stringify(result));
