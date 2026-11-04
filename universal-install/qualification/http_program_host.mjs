import assert from "node:assert/strict";
import http from "node:http";
import {HttpProgramConnection} from "../host-bindings/http-program.mjs";
let calls=0;
let observedRedirectPolicy=[];
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
 for(const invalid of ["https://untrusted.example/health","//untrusted.example/health",
                      "/../health","/%2f/health"]){
   assert.throws(()=>new HttpProgramConnection({...spec,health_path:invalid}),/HTTP_URL_INVALID/);
 }
 assert.throws(()=>new HttpProgramConnection({...spec,operations:{
    evaluate:{method:"POST",path:"//untrusted.example/evaluate"}}}),/HTTP_OPERATION_INVALID/);
 const anonymous=new HttpProgramConnection(spec);
 assert.equal((await anonymous.connect()).status,"AUTHORIZATION_REQUIRED");
 let authToken="SYNTHETIC-CI", authProviderFailed=false;
 const c=new HttpProgramConnection(spec,{
  fetcher:async (url,options)=>{
   observedRedirectPolicy.push(options.redirect);
   return fetch(url,options);
  },
  authHeaders:async()=>{
   if(authProviderFailed)throw Error("HOST_SECRET_STORE_UNAVAILABLE");
   return {Authorization:"Bearer "+authToken};
  }});
 assert.equal(c.inspect().status,"CONFIGURED");
 assert.equal((await c.connect()).status,"CONNECTED");
 assert.equal(c.capabilities().operations[0].verified,false);
 const result=await c.invoke("evaluate",{a:6,b:7});
 assert.equal(result.status,"EXECUTED");
 assert.equal(JSON.parse(result.output).answer,42);
 assert.equal(c.capabilities().operations[0].verified,true);
 assert.equal((await c.invoke("not_declared")).status,"OPERATION_UNSUPPORTED");
 // Real host credentials can become temporarily unavailable while the CLI/API
 // connection remains cached. This must fail closed and revoke verification.
 authProviderFailed=true;
 const priorFetchCount=observedRedirectPolicy.length;
 assert.equal((await c.invoke("evaluate",{a:5,b:6})).status,"AUTHORIZATION_REQUIRED");
 assert.equal(observedRedirectPolicy.length,priorFetchCount,"provider error did not fetch");
 assert.equal(c.capabilities().connected,false);
 assert.equal(c.capabilities().operations[0].verified,false);
 assert.equal((await c.invoke("evaluate",{a:1,b:1})).status,"NOT_CONNECTED");
 assert.equal((await c.recover()).status,"AUTHORIZATION_REQUIRED");
 authProviderFailed=false;
 assert.equal((await c.recover()).status,"CONNECTED");
 assert.equal(c.capabilities().operations[0].verified,false);
 assert.equal((await c.invoke("evaluate",{a:3,b:4})).status,"EXECUTED");
 assert.equal(c.capabilities().operations[0].verified,true);
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
 assert.equal(calls,3);
 assert.ok(observedRedirectPolicy.length>0);
 assert.ok(observedRedirectPolicy.every(policy=>policy==="error"));
 console.log(JSON.stringify({schema:"IWXA_HTTP_PROGRAM_QUALIFICATION/1",
  status:"PASS",transport:"http",real_local_api:true,answer:42,
  host_owned_authorization:true,unsupported_op_rejected:true,
  uncredentialed_rejected:true,auth_provider_failure_fail_closed:true,
  unsafe_health_url_rejected:true,cross_origin_redirect_blocked:true}));
}finally{await new Promise(resolve=>server.close(resolve));}