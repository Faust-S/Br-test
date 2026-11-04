import {randomUUID} from "node:crypto";
// On-demand HTTP API connector. Host retains authorization and persistence.
// A host-approved path must be root-relative, not an absolute/cross-origin URL.
const safePath=path=>typeof path==="string" && /^\/(?!\/)[A-Za-z0-9/_-]*$/.test(path);
const MAX_BYTES=131072;

// A genuine Fetch Response is read incrementally, so a remote endpoint cannot
// exhaust host memory by streaming an unbounded success body. Lightweight host
// fetchers may only implement text(), which remains supported but not streaming.
async function boundedText(response){
 const length=response.headers?.get?.("content-length");
 if(length!==null&&length!==undefined&&/^\d+$/.test(length)&&Number(length)>MAX_BYTES)
  throw Error("HTTP_OUTPUT_TOO_LARGE");
 if(!response.body||typeof response.body.getReader!=="function"){
  const text=await response.text();
  if(typeof text!=="string"||new TextEncoder().encode(text).byteLength>MAX_BYTES)
   throw Error("HTTP_OUTPUT_TOO_LARGE");
  return text;
 }
 const reader=response.body.getReader();
 const decoder=new TextDecoder();
 let output="",size=0,complete=false;
 try{
  while(true){
   const {done,value}=await reader.read();
   if(done){complete=true;break;}
   if(!(value instanceof Uint8Array))throw Error("HTTP_INVALID_RESPONSE");
   size+=value.byteLength;
   if(size>MAX_BYTES)throw Error("HTTP_OUTPUT_TOO_LARGE");
   output+=decoder.decode(value,{stream:true});
  }
  return output+decoder.decode();
 }finally{
  if(!complete){try{await reader.cancel();}catch{}}
  reader.releaseLock();
 }
}
export class HttpProgramConnection {
 constructor(spec,{fetcher=globalThis.fetch,authHeaders=async()=>({})}={}){
  if(spec?.schema!=="IWXA_HTTP_PROGRAM/1" ||
     !/^[A-Z][A-Z0-9_]{2,79}$/.test(spec.program_id??"")||
     typeof fetcher!=="function"||typeof authHeaders!=="function"||
     !spec.operations||!Object.keys(spec.operations).length)throw Error("HTTP_SPEC_INVALID");
  const url=new URL(spec.base_url);
  if(!["http:","https:"].includes(url.protocol)||url.username||url.password||
     !safePath(spec.health_path))throw Error("HTTP_URL_INVALID");
  for(const [name,op] of Object.entries(spec.operations))
   if(!/^[a-z][a-z0-9_]*$/.test(name)||!["GET","POST"].includes(op.method)||
      !safePath(op.path))throw Error("HTTP_OPERATION_INVALID");
  this.spec=spec;this.fetcher=fetcher;this.authHeaders=authHeaders;
  this.connected=false;this.verified=new Set();this.generation=0;this.connectionEpoch=null;
 }
 observation(status,extra={}){return{schema:"IWXA_PROGRAM_CONNECTION/1.0.0",
  program_id:this.spec.program_id,transport:"http",status,
  ...(this.connected&&this.connectionEpoch&&["CONNECTED","DECLARED","EXECUTED"].includes(status)
    ?{connection_epoch:this.connectionEpoch}:{}),...extra};}
 inspect(){return this.observation("CONFIGURED",{connected:false,installed:false,
  interfaces:["http"],ownership:"EXTERNAL",process:"UNKNOWN"});}
 async request(path,method="GET",payload){
  // Host secret-store or credential-provider failure is an auth loss, never
  // a reason to leave previously verified operations in the connected state.
  let headers;
  try {headers={...await this.authHeaders(this.spec.program_id)};}
  catch{return {ok:false,status:401,hostAuthorizationUnavailable:true};}
  // Reject cross-origin redirects so credentials cannot follow HTTP 3xx.
  const opts={method,headers,signal:AbortSignal.timeout(10000),redirect:"error"};
  if(payload!==undefined){
   let body;
   try{body=JSON.stringify(payload);}catch{return {ok:false,inputInvalid:true};}
   if(typeof body!=="string"||new TextEncoder().encode(body).byteLength>MAX_BYTES)
    return {ok:false,inputInvalid:true};
   opts.body=body;opts.headers["Content-Type"]="application/json";
  }
  try{return await this.fetcher(new URL(path,this.spec.base_url).toString(),opts);}
  catch{return null;}
 }
 async connect(){
  // Starting a new connection epoch invalidates previous capability proofs.
  // Late successes from older connect/invoke cannot revive disconnected state.
  const epoch=++this.generation;
  this.connected=false;this.verified.clear();this.connectionEpoch=null;
  const r=await this.request(this.spec.health_path);
  if(epoch!==this.generation)
   return this.observation("CONNECTION_SUPERSEDED",{connected:this.connected});
  if(!r||!r.ok){this.disconnect();
   return this.observation(r?.status===401||r?.status===403?"AUTHORIZATION_REQUIRED":"HTTP_UNAVAILABLE",
     {connected:false});}
  // A health check is not proof of the current identity or any capability.
  this.connected=true;this.connectionEpoch=randomUUID();
  return this.observation("CONNECTED",{connected:true});
 }
 verify(){return this.connected?this.connect():this.observation("NOT_CONNECTED",{connected:false});}
 capabilities(){return this.observation(this.connected?"DECLARED":"NOT_CONNECTED",{
  connected:this.connected,operations:Object.keys(this.spec.operations).sort().map(name=>
   ({name,declared:true,verified:this.connected&&this.verified.has(name)}))});}
 async invoke(name,payload={}){
  if(!this.connected)return this.observation("NOT_CONNECTED",{executed:false});
  const op=this.spec.operations[name];
  if(!op)return this.observation("OPERATION_UNSUPPORTED",{executed:false});
  const epoch=this.generation;
  const r=await this.request(op.path,op.method,op.method==="POST"?payload:undefined);
  if(epoch!==this.generation)
   return this.observation("CONNECTION_SUPERSEDED",{executionOutcome:"UNKNOWN"});
  if(r?.inputInvalid)return this.observation("HTTP_INPUT_INVALID",{executed:false});
  if(!r){this.disconnect();
   return this.observation("HTTP_UNAVAILABLE",{executionOutcome:"UNKNOWN"});}
  if(r.status===401||r.status===403){
   this.disconnect();return this.observation("AUTHORIZATION_REQUIRED",{executed:false});
  }
  if(!r.ok){this.verified.delete(name);
   return this.observation("HTTP_EXECUTION_FAILED",
    {executionOutcome:"UNKNOWN",http_status:r.status});}
  let body;
  try{body=await boundedText(r);}
  catch(error){
   if(epoch!==this.generation)
    return this.observation("CONNECTION_SUPERSEDED",{executionOutcome:"UNKNOWN"});
   this.disconnect();
   const status=error?.message==="HTTP_OUTPUT_TOO_LARGE"?"HTTP_OUTPUT_TOO_LARGE":"HTTP_RESPONSE_UNAVAILABLE";
   return this.observation(status,{executionOutcome:"UNKNOWN"});
  }
  if(epoch!==this.generation)
   return this.observation("CONNECTION_SUPERSEDED",{executionOutcome:"UNKNOWN"});
  this.verified.add(name);
  return this.observation("EXECUTED",{executed:true,operation:name,output:body});
 }
 disconnect(){this.generation++;this.connected=false;this.verified.clear();this.connectionEpoch=null;
  return this.observation("DISCONNECTED",{connected:false});}
 recover(){return this.connect();}
}