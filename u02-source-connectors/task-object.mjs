import {SOURCE_SCHEMA,OBSERVATION_AUTHORITY,sourceDescriptor,sourceObservation,requirePinnedSource} from './port.mjs';
import {captureSource} from './source-read.mjs';

export const TASK_SOURCE_SCHEMA='IWXA_U02_TASK_SOURCE_LINK/1.0.0';
const HEX64=/^[a-f0-9]{64}$/;
const revision=value=>typeof value==='string'&&(/^[a-f0-9]{40,64}$/i.test(value)||/^sha256:[a-f0-9]{64}$/i.test(value));
const identifier=value=>typeof value==='string'&&/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(value);
const objectKind=value=>typeof value==='string'&&value.length<=128&&/^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$/.test(value);
const representations=new Set(['text','html','json','binary','visual_frame']);
const own=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const fields=['programId','instanceRef','sourceId','objectRef','objectKind','representation'];
const equalObject=(a,b)=>own(a)&&own(b)&&fields.every(k=>a[k]===b[k]);
const validObject=value=>own(value)&&Object.keys(value).length===fields.length&&fields.every(k=>Object.hasOwn(value,k))&&
  /^[A-Z][A-Z0-9_]{2,79}$/.test(value.programId)&&identifier(value.instanceRef)&&identifier(value.sourceId)&&
  typeof value.objectRef==='string'&&value.objectRef.length>0&&value.objectRef.length<=1024&&
  !/[\x00-\x1f\x7f]/.test(value.objectRef)&&value.objectRef.normalize('NFC')===value.objectRef&&
  objectKind(value.objectKind)&&representations.has(value.representation)&&
  (value.objectKind!=='browser.page'||['text','html'].includes(value.representation))&&
  (value.objectKind!=='browser.frame'||value.representation==='visual_frame')&&
  (value.representation!=='visual_frame'||value.objectKind==='browser.frame');
const statusObservation=(descriptor,status)=>sourceObservation(descriptor,{status,observedAt:new Date().toISOString(),
  provenance:{provider:'u02.object',authority:'OBSERVATION_ONLY'}});
async function fingerprint(value){
  const subtle=globalThis.crypto?.subtle;
  if(!subtle||typeof subtle.digest!=='function')throw Error('SOURCE_CRYPTO_UNAVAILABLE');
  const stable=x=>Array.isArray(x)?x.map(stable):own(x)?Object.fromEntries(Object.keys(x).sort().map(k=>[k,stable(x[k])])):x;
  const bytes=new TextEncoder().encode(JSON.stringify(stable(value)));
  const digest=new Uint8Array(await subtle.digest('SHA-256',bytes));
  return Array.from(digest,x=>x.toString(16).padStart(2,'0')).join('');
}
function validateBase(descriptor){
  if(descriptor?.schema!==SOURCE_SCHEMA||descriptor.authority!==OBSERVATION_AUTHORITY||
     typeof descriptor.sourceRef!=='string'||typeof descriptor.providerId!=='string'||
     !Array.isArray(descriptor.capabilities))throw Error('TASK_SOURCE_PORT_INVALID');
}
function validateObservation(descriptor,observation){
  if(observation?.schema!==SOURCE_SCHEMA||observation.providerId!==descriptor.providerId||
     observation.sourceRef!==descriptor.sourceRef||observation.observationType!==descriptor.observationType||
     observation.authority!==OBSERVATION_AUTHORITY||typeof observation.status!=='string'||
     !own(observation.provenance))throw Error('TASK_SOURCE_OBSERVATION_INVALID');
}

// Binds the exact object to an existing read-only port without copying its runtime.
// The host supplies the program instance and object identifier; U02 never discovers
// credentials, performs writes or opens an application window.
export async function bindSourceObject(port, object){
  if(typeof port?.describe!=='function'||typeof port?.refresh!=='function'||typeof port?.read!=='function'||
     !validObject(object))throw Error('TASK_SOURCE_OBJECT_INVALID');
  const base=port.describe();validateBase(base);
  const bound=Object.freeze({...object});
  const hash=await fingerprint([base.providerId,base.sourceRef,base.observationType,...fields.map(k=>bound[k])]);
  const descriptor=sourceDescriptor({providerId:base.providerId,
    sourceRef:'u02-object://sha256/'+hash,observationType:base.observationType,
    capabilities:base.capabilities});
  const wrap=observation=>{
    validateObservation(base,observation);
    return sourceObservation(descriptor,{...observation,
      provenance:{...observation.provenance,objectBinding:{...bound},
        underlyingSourceRef:base.sourceRef}});
  };
  return Object.freeze({
    objectBinding:bound,
    describe:()=>descriptor,
    refresh:async()=>wrap(await port.refresh()),
    read:async pinned=>{
      if(!requirePinnedSource(descriptor,pinned))return statusObservation(descriptor,'REVISION_REQUIRED');
      const innerPin=sourceDescriptor({...base,revision:pinned.revision});
      return wrap(await port.read(innerPin));
    }
  });
}

const ownerRevisionValid=value=>value===null||
  (Number.isSafeInteger(value)&&value>=0)||
  (typeof value==='string'&&value.length>0&&value.length<=128&&!/[\x00-\x1f\x7f]/.test(value));
const optionalRef=value=>value===null||
  (typeof value==='string'&&value.length>0&&value.length<=512&&!/[\x00-\x1f\x7f]/.test(value));
const evidenceIdentity=link=>[
  link.taskRef,link.programId,link.instanceRef,link.sourceId,link.objectRef,
  link.objectKind,link.representation,link.sourceRef,link.providerId,link.revision,link.contentRef,
  link.ownerRevision,link.bytes,link.observedAt,link.provenance
];

// Returns a compact reference for P01/P04. The content stays in the host's
// existing storage/projection; no source bytes or executable authority travel.
export async function linkTaskSource(port,{taskRef,ownerRevision=null,
  u03ObservationRef=null,u04BindingRef=null}={}){
  if(typeof taskRef!=='string'||!/^TASK-[A-Za-z0-9_-]+$/.test(taskRef)||
     !ownerRevisionValid(ownerRevision)||!optionalRef(u03ObservationRef)||!optionalRef(u04BindingRef))
    throw Error('TASK_SOURCE_LINK_INVALID');
  const object=port?.objectBinding;
  if(!validObject(object))throw Error('TASK_SOURCE_OBJECT_REQUIRED');
  const capture=await captureSource(port);
  if(capture.status!=='OK')return {status:capture.status,link:null};
  if(!capture.pin||!capture.read)return {status:'READ_UNSUPPORTED',link:null};
  if(capture.read.status!=='OK')return {status:capture.read.status,link:null};
  const observed=capture.read;
  if(!equalObject(observed.provenance?.objectBinding,object)||
     observed.revision!==capture.pin.revision||!revision(observed.revision)||
     typeof observed.contentRef!=='string'||!/^sha256:[a-f0-9]{64}$/i.test(observed.contentRef))
    throw Error('TASK_SOURCE_LINK_INTEGRITY_INVALID');
  const link={schema:TASK_SOURCE_SCHEMA,taskRef,...object,
    ownerRevision,sourceRef:capture.pin.sourceRef,providerId:capture.pin.providerId,
    revision:capture.pin.revision,contentRef:observed.contentRef,
    bytes:observed.bytes,observedAt:observed.observedAt,provenance:observed.provenance,
    pin:capture.pin,authority:OBSERVATION_AUTHORITY,
    evidenceStatus:'SOURCE_OBSERVED_NOT_INDEPENDENTLY_VERIFIED',
    u03ObservationRef,u04BindingRef};
  link.evidenceRef='U02E-'+await fingerprint(evidenceIdentity(link));
  return {status:'OK',link:Object.freeze(link)};
}

// Only latest metadata is compared. Historical pins are never automatically
// promoted to the latest source. UNKNOWN is distinct from STALE.
export async function assessTaskSourceFreshness(port,link){
  if(link?.schema!==TASK_SOURCE_SCHEMA||link.authority!==OBSERVATION_AUTHORITY||
     !validObject(port?.objectBinding)||!equalObject(port.objectBinding,link)||
     typeof link.taskRef!=='string'||!/^TASK-[A-Za-z0-9_-]+$/.test(link.taskRef)||
     !ownerRevisionValid(link.ownerRevision)||!revision(link.revision)||
     typeof link.contentRef!=='string'||!/^sha256:[a-f0-9]{64}$/i.test(link.contentRef)||
     typeof link.evidenceRef!=='string'||
     !HEX64.test(link.evidenceRef.slice(5))||link.evidenceRef.length!==69||
     !equalObject(link.provenance?.objectBinding,link)||
     link.evidenceRef!=='U02E-'+await fingerprint(evidenceIdentity(link))||
     !requirePinnedSource(port.describe(),link.pin)||link.revision!==link.pin.revision||
     link.sourceRef!==port.describe().sourceRef||link.providerId!==port.describe().providerId)
    return {status:'SOURCE_MISMATCH',currentRevision:null};
  let observed;
  try{observed=await port.refresh();}
  catch{return {status:'UNKNOWN',reason:'SOURCE_UNAVAILABLE',currentRevision:null};}
  if(observed?.schema!==SOURCE_SCHEMA||observed.sourceRef!==link.sourceRef||
     observed.providerId!==link.providerId||observed.authority!==OBSERVATION_AUTHORITY)
    return {status:'UNKNOWN',reason:'SOURCE_OBSERVATION_INVALID',currentRevision:null};
  if(observed.status!=='OK'||!revision(observed.revision))
    return {status:'UNKNOWN',reason:observed.status==='OK'?'REVISION_UNAVAILABLE':observed.status,
      currentRevision:null};
  return {status:observed.revision===link.revision?'CURRENT':'STALE',
    previousRevision:link.revision,currentRevision:observed.revision};
}
