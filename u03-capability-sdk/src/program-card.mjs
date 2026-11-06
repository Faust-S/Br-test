// Non-normative Programs Center view over U01/U02/U03/U04 evidence.
// I0-I5 remain owned by IWXA_UI_INTEGRATION/1.0.0 (U01).
// Neither a capability registry nor an invocation/embedding authority.
import {validateDescriptor,validateStatus} from './capability.mjs';
import {projectU01Capabilities} from './u01-bridge.mjs';

const LEVELS=['I0','I1','I2','I3','I4','I5'];
const CHANNELS=Object.freeze({
  DISCOVERY:['I0','U01'],
  DATA:['I1','U02'],
  OPERATION:['I2','U01'],
  LINKED_WINDOW:['I3','U04'],
  EMBEDDED_VIEW:['I4','U04'],
  SHARED_LIVE_SURFACE:['I4','U04'],
  NATIVE_CONTRACT:['I5','HOST'],
});
const BRIDGE='IWXA_PRESENTATION_BRIDGE/1.0.0';
const SURFACE='IWXA_PRESENTATION_SURFACE/1.0.0';
const PROGRAM='IWXA_PROGRAM_CONNECTION/1.0.0';
const STATUS='IWXA_CAPABILITY_STATUS/1.0.0';
const UI='IWXA_UI_INTEGRATION/1.0.0';
const opPattern=/^[a-z][a-z0-9_]*$/;
const programPattern=/^[A-Z][A-Z0-9_]{2,79}$/;
const scoped=/^[A-Za-z0-9_-]{16,128}$/;
const object=x=>x!==null&&typeof x==='object'&&!Array.isArray(x);
const ref=x=>typeof x==='string'&&x.trim().length>0&&x.length<=512;
const invalid=()=>{throw new TypeError('U03_PROGRAM_CARD_INPUT_INVALID');};
const forbidden=new Set(['permissions','grants','invoke','install','connectedByU03']);

function assertUI(snapshot){
  if(snapshot===null)return;
  if(!object(snapshot)||snapshot.schema!==UI||!Array.isArray(snapshot.levels)||
    snapshot.levels.length!==LEVELS.length||
    !LEVELS.every((level,i)=>snapshot.levels[i]?.level===level&&
      typeof snapshot.levels[i].verified==='boolean')||
    snapshot.highestVerified!==(LEVELS.filter((_,i)=>snapshot.levels[i].verified).at(-1)??null))
    invalid();
}
function assertChannels(descriptor,channels){
  if(!Array.isArray(channels)||channels.length!==descriptor.provides.length)invalid();
  const declared=new Map(descriptor.provides.map(row=>[row.id,row.version]));
  const seen=new Set(),operations=new Set();
  for(const row of channels){
    if(!object(row)||!Object.hasOwn(CHANNELS,row.kind)||
      Object.keys(row).some(k=>!['id','kind','operation','target'].includes(k))||
      !declared.has(row.id)||seen.has(row.id)||
      ('target'in row&&typeof row.target!=='boolean'))invalid();
    if(row.kind==='OPERATION'){
      if(typeof row.operation!=='string'||!opPattern.test(row.operation)||
        operations.has(row.operation))invalid();
      operations.add(row.operation);
    } else if('operation'in row)invalid();
    seen.add(row.id);
  }
}
function assertStatuses(statuses){
  if(!Array.isArray(statuses)||statuses.length>128||
    !statuses.every(validateStatus)||new Set(statuses.map(x=>x.id)).size!==statuses.length)
    invalid();
}
function initial(row,version){
  const [level,owner]=CHANNELS[row.kind];
  return {id:row.id,kind:row.kind,level,version,owner,
    state:'DECLARED',verified:false,executionConfirmed:false,
    firstUseCandidate:false,observationSource:null,observationRef:null,
    reason:row.target===true?'TARGET_NOT_QUALIFIED':'OBSERVATION_MISSING',
    target:row.target===true};
}
function setState(value,state,reason=null,source=null,reference=null){
  value.state=state;value.reason=reason;
  value.verified=state==='AVAILABLE'||state==='EXECUTABLE'||state==='INTEGRATED'&&ref(reference);
  value.observationSource=source;
  value.observationRef=ref(reference)?reference:null;
  return value;
}
function dataState(row,status,ui){
  if(!status)return row;
  if(!status.available) return setState(row,'BLOCKED',status.reason??'CAPABILITY_UNAVAILABLE',STATUS);
  if(!status.observationRef||!status.version)
    return setState(row,'BLOCKED','CAPABILITY_UNVERIFIED',STATUS);
  if(status.version!==row.version)
    return setState(row,'BLOCKED','CAPABILITY_VERSION_MISMATCH',STATUS);
  if(row.kind==='NATIVE_CONTRACT'&&!ui?.levels?.[5]?.verified)
    return setState(row,'BLOCKED','NATIVE_CONTRACT_NOT_OBSERVED',STATUS);
  return setState(row,'AVAILABLE',null,STATUS,status.observationRef);
}
function discovered(row,inspect){
  if(!object(inspect)||inspect.schema!==PROGRAM)return row;
  if(inspect.status==='DISCOVERED'&&inspect.installed===true)
    return setState(row,'AVAILABLE',null,PROGRAM,null);
  if(inspect.status==='CONFIGURED')
    return setState(row,'INTEGRATED','CONFIGURED_NOT_INSTALLED',PROGRAM,null);
  return setState(row,'BLOCKED',inspect.status??'PROGRAM_NOT_DISCOVERED',PROGRAM);
}
function invocation(row,observed,status,operation,programId){
  if(!observed)return row;
  const sourceValid=object(observed)&&observed.schema===PROGRAM&&
    observed.program_id===programId&&observed.status==='DECLARED'&&
    observed.connected===true&&typeof observed.connection_epoch==='string'&&
    scoped.test(observed.connection_epoch)&&Array.isArray(observed.operations);
  const found=sourceValid?observed.operations.find(x=>x?.name===operation):null;
  row.firstUseCandidate=Boolean(found?.declared===true&&found.verified===false);
  if(status.available){
    if(status.version!==row.version)return setState(row,'BLOCKED','CAPABILITY_VERSION_MISMATCH',PROGRAM);
    row.executionConfirmed=true;
    return setState(row,'EXECUTABLE',null,PROGRAM,status.observationRef);
  }
  if(status.reason==='operation-unverified'&&row.firstUseCandidate)
    return setState(row,'INTEGRATED','operation-unverified',PROGRAM);
  return setState(row,'BLOCKED',status.reason??'OPERATION_UNAVAILABLE',PROGRAM);
}
function present(row,presentation){
  const mode=row.kind;
  if(!object(presentation)||!object(presentation.capabilities)||
     presentation.capabilities.schema!==BRIDGE||!Array.isArray(presentation.capabilities.capabilities))
    return row;
  const capability=presentation.capabilities.capabilities.find(c=>c?.mode===mode);
  if(!capability)return row;
  if(capability.supported!==true)
    return setState(row,'BLOCKED',capability.reason??'PRESENTATION_UNSUPPORTED',BRIDGE);
  const actual=presentation.observations?.[mode];
  if(!object(actual))return row;
  const binding=actual.binding;
  if(!object(binding)||binding.appRef!==presentation.appRef||
     binding.hostRef!==presentation.hostRef||!ref(binding.bindingRef))
    return setState(row,'BLOCKED','PRESENTATION_BINDING_MISMATCH',BRIDGE);
  if(mode==='LINKED_WINDOW'){
    if(actual.schema!==BRIDGE||
      !ref(binding.windowRef)||!Number.isSafeInteger(binding.processId)||
      binding.processId<=0)
      return setState(row,'BLOCKED','WINDOW_PROOF_INVALID',BRIDGE);
    if(actual.status==='WINDOW_FOCUSED'&&actual.focused===true)
      return setState(row,'AVAILABLE',null,BRIDGE,binding.bindingRef);
    if(actual.status==='WINDOW_OBSERVED')
      return setState(row,'INTEGRATED','WINDOW_OBSERVED_FOCUS_NOT_CONFIRMED',BRIDGE,binding.bindingRef);
    return setState(row,'BLOCKED',actual.status??'WINDOW_NOT_OBSERVED',BRIDGE);
  }
  if(actual.schema!==SURFACE||binding.mode!==mode||
     binding.official!==true||binding.verified!==true||
     binding.existingRuntime!==true||binding.spawned!==false||
     binding.mounted!==true||!ref(binding.mountRef)||
     binding.presentation?.official!==true)
    return setState(row,'BLOCKED','OFFICIAL_SURFACE_PROOF_INVALID',SURFACE);
  if(actual.status==='SURFACE_LIVE')
    return setState(row,'AVAILABLE',null,SURFACE,binding.bindingRef);
  if(actual.status==='SURFACE_MOUNTED')
    return setState(row,'INTEGRATED','SURFACE_LIVE_RECHECK_REQUIRED',SURFACE,binding.bindingRef);
  return setState(row,'BLOCKED',actual.status??'SURFACE_NOT_LIVE',SURFACE);
}

/**
 * Stable host-local DTO, NOT an IWXA normative contract.
 * descriptor: existing U03 ModuleDescriptor
 * channels: host display metadata, one row for each declared provides[]
 * statuses: current U03 CapabilityStatus observations for DATA/NATIVE
 * u01: {inspect,capabilities,receipts}; operations are re-projected through U01 bridge
 * ui: existing integrationObservation() output; never used to certify a capability
 * presentation: {appRef,hostRef,capabilities,observations} from U04 owner
 */
export function projectProgramCard({programId,descriptor,channels,statuses=[],
  u01=null,ui=null,presentation=null}={}){
  if(!validateDescriptor(descriptor)||typeof programId!=='string'||
     !programPattern.test(programId))invalid();
  assertChannels(descriptor,channels);assertStatuses(statuses);assertUI(ui);
  if(u01!==null&&!object(u01))invalid();
  if(presentation!==null&&!object(presentation))invalid();
  const statusIndex=new Map(statuses.map(x=>[x.id,x]));
  const declared=new Map(descriptor.provides.map(x=>[x.id,x.version]));
  const operations=channels.filter(x=>x.kind==='OPERATION');
  const opStatus=new Map();
  if(operations.length&&u01?.capabilities){
    const config={programId,bindings:operations.map(x=>({
      id:x.id,operation:x.operation,version:declared.get(x.id)})),
      receipts:u01.receipts??{}};
    for(const status of projectU01Capabilities(u01.capabilities,config))
      opStatus.set(status.id,status);
  }
  const capabilities=channels.map(binding=>{
    const row=initial(binding,declared.get(binding.id));
    if(binding.kind==='DISCOVERY')return discovered(row,u01?.inspect);
    if(binding.kind==='DATA'||binding.kind==='NATIVE_CONTRACT')
      return dataState(row,statusIndex.get(binding.id),ui);
    if(binding.kind==='OPERATION'){
      if(!opStatus.has(binding.id))return row;
      return invocation(row,u01.capabilities,opStatus.get(binding.id),binding.operation,programId);
    }
    return present(row,presentation);
  });
  return {programId,levels:ui?structuredClone(ui.levels):null,
    highestVerified:ui?.highestVerified??null,capabilities};
}
