import {readFileSync} from "node:fs";
import {CliConnection,SCHEMA} from "../host-bindings/program-connection.mjs";

const [catalogPath,programId,executable]=process.argv.slice(2);
const catalog=JSON.parse(readFileSync(catalogPath,"utf8"));
const spec=catalog.programs.find(p=>p.program_id===programId);
if(!spec || !executable)throw Error("PROGRAM_HOST_ARGUMENT_INVALID");
const start=performance.now();
const connection=new CliConnection(spec,executable);
const discovery=connection.inspect();
if(discovery.status!=="DISCOVERED")throw Error("NODE_DISCOVERY_NOT_FOUND");
const connected=connection.connect();
if(connected.status!=="CONNECTED")throw Error("NODE_CONNECT_FAILED");
const ready=connection.capabilities();
if(ready.operations.length!==1 || ready.operations[0].verified!==false)throw Error("NODE_CAPABILITY_FALSE_CLAIM");
const executed=connection.invoke("evaluate");
if(executed.status!=="EXECUTED" || executed.output.trim()!=="42")throw Error("NODE_INVOKE_FAILED");
if(connection.invoke("unlisted").status!=="OPERATION_UNSUPPORTED")throw Error("NODE_UNLISTED_OPERATION");
if(connection.disconnect().status!=="DISCONNECTED")throw Error("NODE_DISCONNECT_FAILED");
if(connection.invoke("evaluate").status!=="NOT_CONNECTED")throw Error("NODE_DISCONNECTED_EXECUTION");
if(connection.recover().status!=="CONNECTED")throw Error("NODE_RECOVER_FAILED");
if(connection.verify().status!=="CONNECTED")throw Error("NODE_VERIFY_FAILED");
connection.disconnect();
const result={schema:SCHEMA,host:"node",program_id:programId,status:"PASS",
  executed:true,output:executed.output.trim(),cold_ms:Math.round((performance.now()-start)*1000)/1000,
  connector_import_without_python:true,connected_again:true};
console.log(JSON.stringify(result));
