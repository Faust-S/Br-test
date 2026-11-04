import assert from "node:assert/strict";
import http from "node:http";
import {HttpProgramConnection} from "../host-bindings/http-program.mjs";
let calls=0;
const server=http.createServer(async(req,res)=>{
 if(req.headers.authorization!=="Bearer SYNTHETIC-CI"){
  res.writeHead(401);res.end("unauthorized");return;
 }
 if(req.url==="/health"&&req.method==="GET"){
  res.writeHead(200);res.end("ready");return;
 }
 if(req.url==="/evaluate"&&req.method==="POST"){
  let data="";for await(const part of req)data+=part;
  const input=JSON.parse(data);calls++;
  res.writeHead(200,{"content-type":"application/json"});
  res.end(JSON.stringify({answer:input.a*input.b}));return;
 }
 res.writeHead(404);res.end("not found");
});
await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
try{
 const spec={schema:"IWXA_HTTP_PROGRAM/1",program_id:"HTTP_FIXTURE_V1",
  base_url:"http://127.0.0.1:"+server.address().port,health_path:"/health",
  operations:{evaluate:{method:"POST",path:"/evaluate"}}};
 const anonymous=new HttpProgramConnection(spec);
 assert.equal((await anonymous.connect()).status,"AUTHORIZATION_REQUIRED");
 let authToken="SYNTHETIC-CI";
 const c=new HttpProgramConnection(spec,{
  authHeaders:async()=>({Authorization:"Bearer "+authToken})});
 assert.equal(c.inspect().status,"CONFIGURED");
 assert.equal((await c.connect()).status,"CONNECTED");
 assert.equal(c.capabilities().operations[0].verified,false);
 const result=await c.invoke("evaluate",{a:6,b:7});
 assert.equal(result.status,"EXECUTED");
 assert.equal(JSON.parse(result.output).answer,42);
 assert.equal(c.capabilities().operations[0].verified,true);
 assert.equal((await c.invoke("not_declared")).status,"OPERATION_UNSUPPORTED");
 authToken="EXPIRED";
 assert.equal((await c.invoke("evaluate",{a:3,b:4})).status,"AUTHORIZATION_REQUIRED");
 assert.equal(c.capabilities().connected,false);
 assert.equal(c.capabilities().operations[0].verified,false);
 authToken="SYNTHETIC-CI";
 assert.equal((await c.recover()).status,"CONNECTED");
 assert.equal(c.capabilities().operations[0].verified,false);
 assert.equal((await c.invoke("evaluate",{a:3,b:4})).status,"EXECUTED");
 assert.equal(c.capabilities().operations[0].verified,true);
 assert.equal((await c.verify()).status,"CONNECTED");
 assert.equal(c.capabilities().operations[0].verified,false);
 assert.equal(c.disconnect().status,"DISCONNECTED");
 assert.equal((await c.invoke("evaluate",{a:2,b:3})).status,"NOT_CONNECTED");
 assert.equal((await c.recover()).status,"CONNECTED");
 assert.equal(calls,2);
 console.log(JSON.stringify({schema:"IWXA_HTTP_PROGRAM_QUALIFICATION/1",
  status:"PASS",transport:"http",real_local_api:true,answer:42,
  host_owned_authorization:true,unsupported_op_rejected:true,
  uncredentialed_rejected:true}));
}finally{await new Promise(resolve=>server.close(resolve));}