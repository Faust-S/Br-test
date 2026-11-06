// Host-neutral JS/TS CLI binding. No Python core, daemon, network or store.
import {spawnSync} from "node:child_process";
import {accessSync, closeSync, constants, lstatSync, openSync, readSync, realpathSync} from "node:fs";
import {createHash,randomUUID} from "node:crypto";
import {delimiter, isAbsolute} from "node:path";
import {platform} from "node:os";

export const SCHEMA = "IWXA_PROGRAM_CONNECTION/1.0.0";
const HEX64=/^[a-f0-9]{64}$/;
const name=/^[a-z][a-z0-9_]*$/;
const slot=x=>/^\{([a-z][a-z0-9_]*)\}$/.exec(x)||
  /^[a-z][a-z0-9_-]*=\{([a-z][a-z0-9_]*)\}$/.exec(x);
function pinnedHash(path){
  const before=lstatSync(path,{bigint:true});
  if(!before.isFile()||before.isSymbolicLink()||before.size>1073741824n)
    return null;
  const hash=createHash("sha256"),buffer=Buffer.allocUnsafe(65536);
  const fd=openSync(path,"r");
  try{let count;while((count=readSync(fd,buffer,0,buffer.length,null))>0)
    hash.update(buffer.subarray(0,count));}
  finally{closeSync(fd);}
  const after=lstatSync(path,{bigint:true});
  if(before.dev!==after.dev||before.ino!==after.ino||
     before.size!==after.size||before.mtimeNs!==after.mtimeNs)return null;
  return hash.digest("hex");
}
function observedProcess(spec,probe){
  if(typeof probe!=="function")return {process:"UNKNOWN",running:null};
  let value;
  try{value=probe(spec.program_id);}catch{return {process:"UNKNOWN",running:null};}
  if(value?.program_id!==spec.program_id||value?.verified!==true)
    return {process:"UNKNOWN",running:null};
  if(value.status==="NOT_RUNNING")
    return {process:"NOT_RUNNING",running:false};
  if(value.status!=="RUNNING"||value.spawned!==false||
     !Number.isSafeInteger(value.pid)||value.pid<=0||
     typeof value.processStamp!=="string"||value.processStamp.length<8||
     value.processStamp.length>256||/[\x00-\x1f]/.test(value.processStamp))
    return {process:"UNKNOWN",running:null};
  // Host/U04 must check process executable and start time using its OS adapter.
  return {process:"RUNNING",running:true,process_id:value.pid,
    process_stamp:value.processStamp,process_proof:"HOST_OS_OBSERVATION"};
}

export function validateCliSpec(spec) {
  if (!spec || spec.schema !== "IWXA_CLI_PROGRAM/1" ||
      !/^[A-Z][A-Z0-9_]{2,79}$/.test(spec.program_id ?? "") ||
      typeof spec.executable_name !== "string" ||
      /[/\\]/.test(spec.executable_name) || !spec.executable_name ||
      typeof spec.version_contains !== "string" || !spec.version_contains ||
      !spec.operations || !Object.keys(spec.operations).length ||
      (spec.executable_sha256!==undefined&&!HEX64.test(spec.executable_sha256)) ||
      (spec.requires_running!==undefined&&typeof spec.requires_running!=="boolean"))
    throw Error("CLI_SPEC_INVALID");
  for (const argv of [spec.version_args, ...Object.values(spec.operations)]) {
    if (!Array.isArray(argv) || argv.length > 24 ||
        argv.some(a => typeof a !== "string" || a.length > 2048 || a.includes("\0")))
      throw Error("CLI_ARGUMENTS_INVALID");
  }
  return spec;
}
function bindArgs(args,params={}) {
  if(!params || typeof params!=="object" || Array.isArray(params) ||
     Object.keys(params).length>8)throw Error("CLI_INPUT_INVALID");
  const names=new Set(args.map(x=>slot(x)?.[1]).filter(Boolean));
  if(Object.keys(params).length!==names.size ||
     Object.keys(params).some(k=>!names.has(k) || typeof params[k]!=="string" ||
                              params[k].length>16384 || params[k].includes("\0")))
    throw Error("CLI_INPUT_INVALID");
  return args.map(x=>{
    const m=slot(x);
    if(!m)return x;
    return x.startsWith("{")?params[m[1]]:x.slice(0,x.indexOf("=")+1)+params[m[1]];
  });
}
function versionMatches(value,needle) {
  let index=-1;
  while((index=value.indexOf(needle,index+1))>=0) {
    const before=value[index-1]??"";
    const after=value[index+needle.length]??"";
    if(!/[A-Za-z0-9._+-]/.test(before) &&
       !/[A-Za-z0-9._+-]/.test(after))return true;
  }
  return false;
}
function executableIdentity(path) {
  try {
    const st=lstatSync(path,{bigint:true});
    if(!st.isFile()||st.isSymbolicLink())return null;
    return [st.dev,st.ino,st.size,st.mtimeNs].map(String).join(":");
  }catch{return null;}
}
function run(path,args) {
  const r=spawnSync(path,args,{encoding:"utf8",timeout:20000,maxBuffer:131072,
    shell:false,windowsHide:true});
  if (r.error?.code === "ETIMEDOUT") return {status:"CLI_TIMED_OUT"};
  if (r.error) return {status:"CLI_EXECUTION_FAILED"};
  return {status:r.status===0?"EXECUTED":"EXECUTION_FAILED",
    code:r.status,output:r.stdout??""};
}
function located(name) {
  const windows=platform()==="win32";
  // Windows PATH normally contains program.exe, while manifests often declare
  // the portable command name without an extension (e.g. jq, yq).
  const candidates=windows&&!name.toLowerCase().endsWith(".exe")
    ? [name,name+".exe"] : [name];
  const dirs=(process.env.PATH??"").split(delimiter);
  for (const dir of dirs) {
    if(!dir)continue;
    for(const candidate of candidates){
      const path=dir+(dir.endsWith("/")||dir.endsWith("\\")?"":windows?"\\":"/")+candidate;
      try {const stat=lstatSync(path);if(!stat.isSymbolicLink()&&stat.isFile()) {
        accessSync(path,windows?constants.F_OK:constants.X_OK);
        return realpathSync(path);
      }} catch {}
    }
  }
  return null;
}
export class CliConnection {
  constructor(spec,executable=null,{processProbe=null}={}) {
    this.spec=validateCliSpec(spec);
    this.requested=executable;
    this.path=null;
    this.identity=null;
    this.epoch=null;
    this.processProbe=processProbe;
    this.verified=new Set();
    this.lastUnavailable=null;
  }
  observation(status,detail={}) {return {schema:SCHEMA,program_id:this.spec.program_id,
    transport:"cli",status,
    ...(this.epoch&&["CONNECTED","DECLARED","EXECUTED"].includes(status)
      ?{connection_epoch:this.epoch}:{}),...detail};}
  processObservation(){return observedProcess(this.spec,this.processProbe);}
  installationIdentity(path){
    if(!this.spec.executable_sha256)return {installation_verified:false,
      installation_reason:"PIN_NOT_PROVIDED"};
    let digest;
    try{digest=pinnedHash(path);}catch{return {installation_verified:false,
      installation_reason:"INSTALLATION_READBACK_FAILED"};}
    if(digest!==this.spec.executable_sha256)return {installation_verified:false,
      installation_reason:"EXECUTABLE_DIGEST_MISMATCH"};
    return {installation_verified:true,installation_identity:{
      program_id:this.spec.program_id,executable:path,sha256:digest,
      identity_source:"PINNED_BINARY_SHA256_READBACK",verified:true}};
  }
  inspect() {
    let path=this.requested??located(this.spec.executable_name);
    try {
      if(path && isAbsolute(path) && !lstatSync(path).isSymbolicLink()) {
        accessSync(path,platform()==="win32"?constants.F_OK:constants.X_OK);
        if(lstatSync(path).isFile()){
          const resolved=realpathSync(path);
          const installation=this.installationIdentity(resolved);
          const observation={installed:true,path:resolved,connected:false,
            architecture:"UNKNOWN",ownership:"UNKNOWN",interfaces:["cli"],
            ...installation,...this.processObservation()};
          return this.observation(this.spec.executable_sha256&&!installation.installation_verified
            ?"INSTALLATION_MISMATCH":"DISCOVERED",observation);
        }
      }
    } catch {}
    return this.observation("NOT_FOUND",{installed:false,connected:false,
      architecture:"UNKNOWN",ownership:"UNKNOWN",interfaces:[],
      installation_verified:false,...this.processObservation()});
  }
  connect() {
    const found=this.inspect();
    if(found.status!=="DISCOVERED"){this.disconnect();
      this.lastUnavailable=found.status;
      return found;}
    if(this.spec.requires_running&&found.running!==true){
      this.disconnect();
      const status=found.running===false?"PROCESS_NOT_RUNNING":"RUNNING_PROCESS_UNVERIFIED";
      this.lastUnavailable=status;
      return this.observation(status,{installed:true,connected:false,
        installation_verified:found.installation_verified,...this.processObservation()});
    }
    const identity=executableIdentity(found.path);
    if(!identity){this.disconnect();
      return this.observation("NOT_FOUND",{installed:false,connected:false});}
    if(this.path!==found.path||this.identity!==identity){
      this.verified.clear();this.epoch=null;
    }
    const r=run(found.path,this.spec.version_args);
    if(r.status!=="EXECUTED"||!versionMatches(r.output,this.spec.version_contains)){
      this.disconnect();this.lastUnavailable="VERSION_UNVERIFIED";
      return this.observation("VERSION_UNVERIFIED",{installed:true,connected:false});
    }
    this.path=found.path;
    this.identity=identity;
    if(!this.epoch)this.epoch=randomUUID();
    this.lastUnavailable=null;
    return this.observation("CONNECTED",{installed:true,connected:true,
      path:this.path,version:r.output.trim().slice(0,256),
      ...this.installationIdentity(this.path),...this.processObservation()});
  }
  verify(){
    if(!this.path)return this.observation("NOT_CONNECTED",{connected:false});
    if(executableIdentity(this.path)!==this.identity){
      this.disconnect();return this.observation("CONNECTION_LOST",{connected:false});
    }
    return this.connect();
  }
  capabilities(){
    const lost=!!this.path && executableIdentity(this.path)!==this.identity;
    if(lost)this.disconnect();
    const installed=this.path?this.installationIdentity(this.path):null;
    const mismatch=!!installed&&!!this.spec.executable_sha256&&!installed.installation_verified;
    const running=this.processObservation();
    const processLost=!!this.path&&this.spec.requires_running&&running.running!==true;
    if(mismatch||processLost)this.disconnect();
    if(processLost)this.lastUnavailable=running.running===false?"PROCESS_NOT_RUNNING":"RUNNING_PROCESS_UNVERIFIED";
    return this.observation(lost?"CONNECTION_LOST":mismatch?"INSTALLATION_MISMATCH":
      processLost?this.lastUnavailable:this.path?"DECLARED":"NOT_CONNECTED",{
      connected:!!this.path, ...(installed??{}),...running,
      ...(this.lastUnavailable?{unavailable_reason:this.lastUnavailable}:{}),
      operations:Object.keys(this.spec.operations).sort()
       .map(name=>({name,declared:true,verified:this.verified.has(name)}))});
  }
  invoke(operation,params={}) {
    if(!this.path) return this.observation("NOT_CONNECTED",{executed:false,
      ...(this.lastUnavailable?{unavailable_reason:this.lastUnavailable}:{})});
    if(executableIdentity(this.path)!==this.identity){
      this.disconnect();return this.observation("CONNECTION_LOST",{executed:false});
    }
    if(this.spec.executable_sha256&&!this.installationIdentity(this.path).installation_verified){
      this.disconnect();this.lastUnavailable="INSTALLATION_MISMATCH";
      return this.observation("INSTALLATION_MISMATCH",{executed:false});
    }
    if(this.spec.requires_running){
      const running=this.processObservation();
      if(running.running!==true){
        this.disconnect();
        const status=running.running===false?"PROCESS_NOT_RUNNING":"RUNNING_PROCESS_UNVERIFIED";
        this.lastUnavailable=status;
        return this.observation(status,{executed:false,...running});
      }
    }
    // Only own manifest entries are allowed. Inherited Object methods such as
    // "constructor" or "toString" must never be treated as CLI operations.
    if(typeof operation!=="string"||!Object.hasOwn(this.spec.operations,operation))
      return this.observation("OPERATION_UNSUPPORTED",{executed:false});
    const args=this.spec.operations[operation];
    let bound;
    try{bound=bindArgs(args,params)}catch{
      return this.observation("CLI_INPUT_INVALID",{executed:false});
    }
    const begin=performance.now(),r=run(this.path,bound);
    if(r.status!=="EXECUTED")return this.observation(r.status,{
      executed:false,exit_code:r.code});
    this.verified.add(operation);
    return this.observation("EXECUTED",{executed:true,operation,
      ...this.installationIdentity(this.path),...this.processObservation(),
      output:r.output,elapsed_ms:Math.round((performance.now()-begin)*1000)/1000});
  }
  disconnect(){this.path=null;this.identity=null;this.epoch=null;this.verified.clear();
    return this.observation("DISCONNECTED",{connected:false});}
  recover(){return this.connect();}
}
