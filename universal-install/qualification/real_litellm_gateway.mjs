// Real LiteLLM Gateway process -> existing U01 HTTP connector -> OpenAI-compatible
// synthetic upstream. Only the gateway is real; the model reply is a fixture.
import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {createServer} from "node:http";
import {mkdtemp,writeFile,rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {HttpProgramConnection} from "../host-bindings/http-program.mjs";

const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const startServer=server=>new Promise((resolve,reject)=>{
 server.once("error",reject);
 server.listen(0,"127.0.0.1",()=>resolve(server.address().port));
});
const closeServer=server=>new Promise(resolve=>server.close(resolve));
async function freePort(){
 const server=createServer((_req,res)=>res.end("reserved"));
 const port=await startServer(server);
 await closeServer(server);
 return port;
}

let forwarded=0;
const upstream=createServer(async(req,res)=>{
 if(req.method==="POST"&&["/chat/completions","/v1/chat/completions"].includes(req.url)){
  const chunks=[];let size=0;
  for await(const piece of req){size+=piece.length;if(size>131072){res.writeHead(413);res.end();return;}chunks.push(piece);}
  let request;
  try{request=JSON.parse(Buffer.concat(chunks).toString("utf8"));}
  catch{res.writeHead(400);res.end();return;}
  if(!Array.isArray(request.messages)||request.model!=="iwxa-ci-upstream"){
   res.writeHead(422);res.end(JSON.stringify({error:"bad upstream model request"}));return;
  }
  forwarded++;
  res.writeHead(200,{"content-type":"application/json"});
  res.end(JSON.stringify({
   id:"chatcmpl-iwxa-ci-fixture",object:"chat.completion",created:1760000000,
   model:"iwxa-ci-upstream",choices:[{index:0,message:{role:"assistant",content:"42"},
    finish_reason:"stop"}],usage:{prompt_tokens:5,completion_tokens:1,total_tokens:6}
  }));
  return;
 }
 res.writeHead(404);res.end("fixture endpoint absent");
});
let proxy=null,workspace=null,upstreamStarted=false,proxyOutput="";
try{
 const upstreamPort=await startServer(upstream);upstreamStarted=true;
 const proxyPort=await freePort();
 workspace=await mkdtemp(join(tmpdir(),"iwxa-u01-litellm-"));
 const config=join(workspace,"config.yaml");
 await writeFile(config,[
  "model_list:",
  "  - model_name: iwxa-ci-model",
  "    litellm_params:",
  "      model: openai/iwxa-ci-upstream",
  "      api_base: http://127.0.0.1:"+upstreamPort+"/v1",
  "      api_key: sk-iwxa-ci-upstream-synthetic",
  "general_settings:",
  "  master_key: sk-iwxa-ci-gateway-test",
  "litellm_settings:",
  "  num_retries: 0",
  ""
 ].join("\n"),"utf8");
 const spec={schema:"IWXA_HTTP_PROGRAM/1",program_id:"LITELLM_GATEWAY_CI",
  base_url:"http://127.0.0.1:"+proxyPort,health_path:"/v1/models",
  operations:{models:{method:"GET",path:"/v1/models"},
   chat_completions:{method:"POST",path:"/v1/chat/completions"}}};
 const connection=new HttpProgramConnection(spec,{
  authHeaders:async()=>({Authorization:"Bearer sk-iwxa-ci-gateway-test"})});
 assert.equal(connection.inspect().status,"CONFIGURED");
 assert.equal(connection.capabilities().connected,false);
 assert.equal((await connection.connect()).status,"HTTP_UNAVAILABLE",
  "configured but missing gateway must not appear connected");
 assert.equal(connection.capabilities().operations.some(o=>o.verified),false);
 proxy=spawn(process.env.IWXA_LITELLM_BIN??"litellm",
  ["--config",config,"--host","127.0.0.1","--port",String(proxyPort)],
  {env:{...process.env,LITELLM_LOG:"ERROR",NO_COLOR:"1"},
   cwd:workspace,stdio:["ignore","pipe","pipe"]});
 proxy.stdout.on("data",d=>{proxyOutput=(proxyOutput+d.toString()).slice(-16000);});
 proxy.stderr.on("data",d=>{proxyOutput=(proxyOutput+d.toString()).slice(-16000);});
 proxy.once("error",error=>{proxyOutput+="\nSPAWN_ERROR "+error.message;});
 let attached=null;
 for(let attempt=0;attempt<70;attempt++){
  if(proxy.exitCode!==null)break;
  // /v1/models is authenticated. It proves the gateway route exists, not
  // that any upstream model can successfully infer.
  const observation=await connection.connect();
  if(observation.status==="CONNECTED"){attached=observation;break;}
  await sleep(1500);
 }
 assert.ok(attached,"real LiteLLM gateway did not expose the model-list API: "+proxyOutput.slice(-2800));
 assert.match(attached.connection_epoch,/^[a-f0-9-]{36}$/);
 assert.equal(connection.capabilities().connection_epoch,attached.connection_epoch);
 assert.ok(connection.capabilities().operations.every(o=>o.verified===false));
 const listed=await connection.invoke("models",{});
 assert.equal(listed.status,"EXECUTED");
 assert.equal(listed.connection_epoch,attached.connection_epoch);
 const catalogue=JSON.parse(listed.output);
 assert.ok(Array.isArray(catalogue.data)&&catalogue.data.some(x=>x.id==="iwxa-ci-model"));
 const completed=await connection.invoke("chat_completions",{
  model:"iwxa-ci-model",stream:false,
  messages:[{role:"user",content:"Return 42 for the U01 transport test"}]
 });
 assert.equal(completed.status,"EXECUTED",JSON.stringify(completed));
 assert.equal(completed.connection_epoch,attached.connection_epoch);
 const response=JSON.parse(completed.output);
 assert.equal(response.choices?.[0]?.message?.content,"42");
 assert.ok(forwarded>=1,"no request reached the upstream through real LiteLLM");
 assert.equal(connection.capabilities().operations.find(x=>x.name==="chat_completions").verified,true);
 const refreshed=await connection.verify();
 assert.equal(refreshed.status,"CONNECTED");
 assert.notEqual(refreshed.connection_epoch,attached.connection_epoch);
 assert.ok(connection.capabilities().operations.every(x=>x.verified===false));
 connection.disconnect();
 assert.equal(connection.capabilities().connection_epoch,undefined);
 assert.equal((await connection.invoke("chat_completions",{})).status,"NOT_CONNECTED");
 console.log(JSON.stringify({schema:"IWXA_U01_REAL_LITELLM_GATEWAY/1",status:"PASS",
  gateway:"LiteLLM",gateway_real_process:true,upstream:"SYNTHETIC_OPENAI_FIXTURE",
  model_inference_real:false,transport:"http",configuration_only_not_connected:true,
  model_list_readback:true,chat_response_readback:"42",forwarded_requests:forwarded,
  connection_epoch_cleared:true,session_rotation_after_verify:true,
  external_router_optional:true,ollama_not_modified:true}));
}finally{
 if(proxy&&proxy.exitCode===null){
  proxy.kill("SIGTERM");
  for(let i=0;i<20&&proxy.exitCode===null;i++)await sleep(100);
  if(proxy.exitCode===null)proxy.kill("SIGKILL");
 }
 if(upstreamStarted)await closeServer(upstream);
 if(workspace)await rm(workspace,{recursive:true,force:true});
}
