// Read-only U01 -> U03 projection. Host owns program, receipt, and authority.
import {STATUS_SCHEMA} from './capability.mjs';
const U01='IWXA_PROGRAM_CONNECTION/1.0.0';
const id=/^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*(?![\s\S])/;
const ver=/^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?![\s\S])/;
const op=/^[a-z][a-z0-9_]*(?![\s\S])/;
const prog=/^[A-Z][A-Z0-9_]{2,79}(?![\s\S])/;
const scoped=/^[A-Za-z0-9_-]{16,128}(?![\s\S])/;
const transports=new Set(['cli','http','mcp','sdk','ipc','file']);
const obj=x=>x!==null&&typeof x==='object'&&!Array.isArray(x);
const keys=(x,r,o=[])=>obj(x)&&r.every(k=>Object.hasOwn(x,k))&&Object.keys(x).every(k=>[...r,...o].includes(k));
function validate(config){
 if(!keys(config,['programId','bindings'],['receipts'])||
    typeof config.programId!=='string'||!prog.test(config.programId)||
    !Array.isArray(config.bindings)||config.bindings.length<1||config.bindings.length>128||
    !config.bindings.every(x=>keys(x,['operation','id','version'])&&
      typeof x.operation==='string'&&op.test(x.operation)&&
      typeof x.id==='string'&&x.id.length<=128&&id.test(x.id)&&
      typeof x.version==='string'&&x.version.length<=120&&ver.test(x.version))||
    new Set(config.bindings.map(x=>x.operation)).size!==config.bindings.length||
    new Set(config.bindings.map(x=>x.id)).size!==config.bindings.length||
    (Object.hasOwn(config,'receipts')&&!obj(config.receipts)))
   throw new TypeError('U03_U01_BINDINGS_INVALID');
}
function live(x,programId){
 return obj(x)&&x.schema===U01&&x.program_id===programId&&
   transports.has(x.transport)&&x.status==='DECLARED'&&x.connected===true&&
   Array.isArray(x.operations)&&x.operations.length<=128&&
   x.operations.every(o=>keys(o,['name','declared','verified'])&&
     typeof o.name==='string'&&op.test(o.name)&&
     typeof o.declared==='boolean'&&typeof o.verified==='boolean')&&
   new Set(x.operations.map(o=>o.name)).size===x.operations.length;
}
function validReceipt(x,operation,programId,transport,epoch){
 if(!keys(x,['observation','observationRef']))return false;
 const o=x.observation;
 return obj(o)&&o.schema===U01&&o.program_id===programId&&o.transport===transport&&
   o.status==='EXECUTED'&&o.executed===true&&o.operation===operation&&
   o.connection_epoch===epoch&&
   typeof x.observationRef==='string'&&x.observationRef.trim().length>0&&x.observationRef.length<=512;
}
export function observeU01Capabilities(connection,config){
 validate(config);
 let observed;
 try{observed=connection.capabilities();}catch{observed=null;}
 const current=live(observed,config.programId);
 const ops=current?new Map(observed.operations.map(o=>[o.name,o])):new Map();
 const receipts=config.receipts??{};
 return config.bindings.map(binding=>{
   const operation=ops.get(binding.operation);
   let reason='source-unavailable';
   if(current){
     if(typeof observed.connection_epoch!=='string'||!scoped.test(observed.connection_epoch))
       reason='connection-unscoped';
     else if(!operation||!operation.declared)reason='operation-unsupported';
     else if(!operation.verified)reason='operation-unverified';
     else if(!validReceipt(receipts[binding.operation],binding.operation,
               config.programId,observed.transport,observed.connection_epoch))reason='execution-receipt-missing';
     else reason=null;
   }
   const status={schema:STATUS_SCHEMA,id:binding.id,available:reason===null};
   if(reason!==null)status.reason=reason;
   else{status.version=binding.version;status.observationRef=receipts[binding.operation].observationRef;}
   return status;
 });
}
