// On-demand HTTP API connector. Host retains authorization and persistence.
export class HttpProgramConnection {
 constructor(spec,{fetcher=globalThis.fetch,authHeaders=async()=>({})}={}){
  if(spec?.schema!=="IWXA_HTTP_PROGRAM/1" ||
     !/^[A-Z][A-Z0-9_]{2,79}$/.test(spec.program_id??"")||
     typeof fetcher!=="function"||typeof authHeaders!=="function"||
     !spec.operations||!Object.keys(spec.operations).length)throw Error("HTTP_SPEC_INVALID");
  const url=new URL(spec.base_url);
  if(!["http:","https:"].includes(url.protocol)||url.username||url.password)throw Error("HTTP_URL_INVALID");
  for(const [name,op] of Object.entries(spec.operations))
   if(!/^[a-z][a-z0-9_]*$/.test(name)||!["GET","POST"].includes(op.method)||
      !/^\/[A-Za-z0-9/_-]*$/.test(op.path))throw Error("HTTP_OPERATION_INVALID");
  this.spec=spec;this.fetcher=fetcher;this.authHeaders=authHeaders;
  this.connected=false;this.verified=new Set();
 }
 observation(status,extra={}){return{schema:"IWXA_PROGRAM_CONNECTION/1.0.0",
  program_id:this.spec.program_id,transport:"http",status,...extra};}
 inspect(){return this.observation("CONFIGURED",{connected:false,installed:false,
  interfaces:["http"],ownership:"EXTERNAL",process:"UNKNOWN"});}
 async request(path,method="GET",payload){
  const headers={...await this.authHeaders(this.spec.program_id)};
  const opts={method,headers,signal:AbortSignal.timeout(10000)};
  if(payload!==undefined){
   const body=JSON.stringify(payload);
   if(body.length>131072)throw Error("HTTP_INPUT_TOO_LARGE");
   opts.body=body;opts.headers["Content-Type"]="application/json";
  }
  try{return await this.fetcher(new URL(path,this.spec.base_url).toString(),opts);}
  catch{return null;}
 }
 async connect(){
  const r=await this.request(this.spec.health_path);
  if(!r||!r.ok){this.disconnect();
   return this.observation(r?.status===401||r?.status===403?"AUTHORIZATION_REQUIRED":"HTTP_UNAVAILABLE",
     {connected:false});}
  // A successful health request does not establish a stable remote identity.
  // Revalidate previously executed capabilities after an explicit reconnect.
  this.verified.clear();
  this.connected=true;return this.observation("CONNECTED",{connected:true});
 }
 verify(){return this.connected?this.connect():this.observation("NOT_CONNECTED",{connected:false});}
 capabilities(){return this.observation(this.connected?"DECLARED":"NOT_CONNECTED",{
  connected:this.connected,operations:Object.keys(this.spec.operations).sort().map(name=>
   ({name,declared:true,verified:this.verified.has(name)}))});}
 async invoke(name,payload={}){
  if(!this.connected)return this.observation("NOT_CONNECTED",{executed:false});
  const op=this.spec.operations[name];
  if(!op)return this.observation("OPERATION_UNSUPPORTED",{executed:false});
  const r=await this.request(op.path,op.method,op.method==="POST"?payload:undefined);
  if(!r){this.disconnect();
   return this.observation("HTTP_UNAVAILABLE",{executed:false});}
  if(r.status===401||r.status===403){
   this.disconnect();
   return this.observation("AUTHORIZATION_REQUIRED",{executed:false});
  }
  if(!r.ok)return this.observation("HTTP_EXECUTION_FAILED",
   {executed:false,http_status:r.status});
  const body=await r.text();
  if(body.length>131072)return this.observation("HTTP_OUTPUT_TOO_LARGE",{executed:false});
  this.verified.add(name);
  return this.observation("EXECUTED",{executed:true,operation:name,output:body});
 }
 disconnect(){this.connected=false;this.verified.clear();
  return this.observation("DISCONNECTED",{connected:false});}
 recover(){return this.connect();}
}