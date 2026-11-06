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
    firstUseCandidate:false,canOfferInvocation:false,observationSource:null,observationRef:null,
    reason:row.target===true?'TARGET_NOT_QUALIFIED':'OBSERVATION_MISSING',
    target:row.target===true};
}
function setState(value,state,reason=null,source=null,reference=null){
  value.state=state;value.reason=reason;
  value.verified=state==='AVAILABLE'||state==='EXECUTABLE';
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
  row.canOfferInvocation=row.firstUseCandidate;
  if(status.available){
    if(status.version!==row.version)return setState(row,'BLOCKED','CAPABILITY_VERSION_MISMATCH',PROGRAM);
    row.executionConfirmed=true;
    return setState(row,'EXECUTABLE',null,PROGRAM,status.observationRef);
  }
  if(status.reason==='operation-unverified'&&row.firstUseCandidate)
    return setState(row,'INTEGRATED','operation-unverified',PROGRAM);
  return setState(row,'BLOCKED',status.reason??'OPERATION_UNAVAILABLE',PROGRAM);
}

// Consume a SINGLE Core-owned U01/U03 readback, not raw receipts or a new probe.
// This shape is already returned by Personal Agent 0.3 program_use_projection().
function ownerOperationState(row,owner,operation,programId){
  const source=owner.source;
  if(!object(source)||source.schema!==PROGRAM||source.program_id!==programId)
    return setState(row,'BLOCKED','SOURCE_IDENTITY_MISMATCH',PROGRAM);
  const live=source.status==='DECLARED'&&source.connected===true&&
    typeof source.connection_epoch==='string'&&scoped.test(source.connection_epoch)&&
    Array.isArray(source.operations);
  if(!live)return setState(row,'BLOCKED','source-unavailable',PROGRAM);
  const raw=source.operations.filter(x=>x?.name===operation);
  if(raw.length!==1||raw[0].declared!==true)
    return setState(row,'BLOCKED',raw.length>1?'OWNER_OPERATION_AMBIGUOUS':'operation-unsupported',PROGRAM);
  const matched=owner.operations.filter(x=>x?.operation===operation||x?.capability_id===row.id);
  if(matched.length!==1||matched[0].operation!==operation||matched[0].capability_id!==row.id)
    return setState(row,'BLOCKED','OWNER_OPERATION_MISMATCH',STATUS);
  const entry=matched[0],status=entry.observation;
  if(typeof entry.can_offer_invocation!=='boolean'||
     typeof entry.execution_verified!=='boolean'||
     !validateStatus(status)||status.id!==row.id)
    return setState(row,'BLOCKED','OWNER_OBSERVATION_INVALID',STATUS);
  if(entry.execution_verified!==status.available)
    return setState(row,'BLOCKED','OWNER_OBSERVATION_INCONSISTENT',STATUS);
  const declared=raw[0];
  const canOffer=entry.can_offer_invocation===true;
  if(status.available){
    if(declared.verified!==true||!canOffer)
      return setState(row,'BLOCKED','OWNER_OBSERVATION_INCONSISTENT',STATUS);
    if(status.version!==row.version)return setState(row,'BLOCKED','CAPABILITY_VERSION_MISMATCH',STATUS);
    if(!ref(status.observationRef))
      return setState(row,'BLOCKED','CAPABILITY_UNVERIFIED',STATUS);
    row.executionConfirmed=true;
    row.canOfferInvocation=true; // Display hint; P01 still owns execution.
    return setState(row,'EXECUTABLE',null,STATUS,status.observationRef);
  }
  if(canOffer&&((declared.verified===false&&status.reason==='operation-unverified')||
       (declared.verified===true&&status.reason==='execution-receipt-missing'))){
    row.canOfferInvocation=true;
    row.firstUseCandidate=declared.verified===false;
    return setState(row,'INTEGRATED',status.reason,STATUS);
  }
  return setState(row,'BLOCKED',status.reason??'OPERATION_UNAVAILABLE',STATUS);
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
 * u01: {inspect,capabilities,receipts} OR {inspect,ownerProjection} already\n *      checked by the Core owner; no second U01 read or receipt transfer
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
  const owner=u01?.ownerProjection;
  if(owner!==undefined){
    if(u01?.capabilities!==undefined||u01?.receipts!==undefined||
       !object(owner)||owner.program_id!==programId||
       owner.execution_authority_granted!==false||
       !Array.isArray(owner.operations)||owner.operations.length>128)
      invalid();
  }
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
      if(owner!==undefined)return ownerOperationState(row,owner,binding.operation,programId);
      if(!opStatus.has(binding.id))return row;
      return invocation(row,u01.capabilities,opStatus.get(binding.id),binding.operation,programId);
    }
    return present(row,presentation);
  });
  return {programId,levels:ui?structuredClone(ui.levels):null,
    highestVerified:ui?.highestVerified??null,capabilities};
}
