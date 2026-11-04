// Host-neutral JS/TS CLI binding. No Python core, daemon, network or store.
import {spawnSync} from "node:child_process";
import {accessSync, constants, lstatSync, realpathSync} from "node:fs";
import {delimiter, isAbsolute} from "node:path";
import {platform} from "node:os";

export const SCHEMA = "IWXA_PROGRAM_CONNECTION/1.0.0";
export function validateCliSpec(spec) {
  if (!spec || spec.schema !== "IWXA_CLI_PROGRAM/1" ||
      !/^[A-Z][A-Z0-9_]{2,79}$/.test(spec.program_id ?? "") ||
      typeof spec.executable_name !== "string" ||
      /[/\\]/.test(spec.executable_name) || !spec.executable_name ||
      typeof spec.version_contains !== "string" || !spec.version_contains ||
      !spec.operations || !Object.keys(spec.operations).length)
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
  const names=new Set(args.filter(x=>/^\{[a-z][a-z0-9_]*\}$/.test(x))
    .map(x=>x.slice(1,-1)));
  if(Object.keys(params).length!==names.size ||
     Object.keys(params).some(k=>!names.has(k) || typeof params[k]!=="string" ||
                              params[k].length>16384 || params[k].includes("\0")))
    throw Error("CLI_INPUT_INVALID");
  return args.map(x=>/^\{[a-z][a-z0-9_]*\}$/.test(x)?params[x.slice(1,-1)]:x);
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
  constructor(spec,executable=null) {
    this.spec=validateCliSpec(spec);
    this.requested=executable;
    this.path=null;
    this.identity=null;
    this.verified=new Set();
  }
  observation(status,detail={}) {return {schema:SCHEMA,program_id:this.spec.program_id,
    transport:"cli",status,...detail};}
  inspect() {
    let path=this.requested??located(this.spec.executable_name);
    try {
      if(path && isAbsolute(path) && !lstatSync(path).isSymbolicLink()) {
        accessSync(path,platform()==="win32"?constants.F_OK:constants.X_OK);
        if(lstatSync(path).isFile()) return this.observation("DISCOVERED",{
          installed:true,path:realpathSync(path),connected:false,
          architecture:"UNKNOWN",ownership:"UNKNOWN",interfaces:["cli"],
          process:"UNKNOWN"});
      }
    } catch {}
    return this.observation("NOT_FOUND",{installed:false,connected:false,
      architecture:"UNKNOWN",ownership:"UNKNOWN",interfaces:[],process:"UNKNOWN"});
  }
  connect() {
    const found=this.inspect();
    if(!found.installed) {this.disconnect();return found;}
    const identity=executableIdentity(found.path);
    if(!identity){this.disconnect();
      return this.observation("NOT_FOUND",{installed:false,connected:false});}
    if(this.path!==found.path||this.identity!==identity)this.verified.clear();
    const r=run(found.path,this.spec.version_args);
    if(r.status!=="EXECUTED"||!versionMatches(r.output,this.spec.version_contains)){
      this.disconnect();return this.observation("VERSION_UNVERIFIED",{installed:true,connected:false});
    }
    this.path=found.path;
    this.identity=identity;
    return this.observation("CONNECTED",{installed:true,connected:true,
      path:this.path,version:r.output.trim().slice(0,256)});
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
    return this.observation(lost?"CONNECTION_LOST":this.path?"DECLARED":"NOT_CONNECTED",{
      connected:!!this.path,operations:Object.keys(this.spec.operations).sort()
       .map(name=>({name,declared:true,verified:this.verified.has(name)}))});
  }
  invoke(operation,params={}) {
    if(!this.path) return this.observation("NOT_CONNECTED",{executed:false});
    if(executableIdentity(this.path)!==this.identity){
      this.disconnect();return this.observation("CONNECTION_LOST",{executed:false});
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
      output:r.output,elapsed_ms:Math.round((performance.now()-begin)*1000)/1000});
  }
  disconnect(){this.path=null;this.identity=null;this.verified.clear();return this.observation("DISCONNECTED",{connected:false});}
  recover(){return this.connect();}
}
