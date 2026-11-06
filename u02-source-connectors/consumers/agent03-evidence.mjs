import {TASK_SOURCE_SCHEMA,assessTaskSourceFreshness} from '../task-object.mjs';

const taskIdValid=value=>typeof value==='string'&&/^TASK-[A-Za-z0-9_-]+$/.test(value);
const ownerRef=(value)=>value&&typeof value==='object'&&!Array.isArray(value)?value:null;

// A compact, optional P01 source reference. Never inject U02's full envelope
// into Agent Task/Result 0.5 or the strict composer knowledge_sources array.
export async function projectAgent03SourceEvidence(port,link,{taskId}={}){
  if(!taskIdValid(taskId))throw Error('AGENT03_TASK_ID_INVALID');
  if(link?.schema!==TASK_SOURCE_SCHEMA||link.taskRef!==taskId)
    return {status:'TASK_MISMATCH',sourceRef:null,canUseCurrent:false};
  const assessment=await assessTaskSourceFreshness(port,link);
  if(assessment.status==='SOURCE_MISMATCH')
    return {status:'SOURCE_MISMATCH',sourceRef:null,canUseCurrent:false};
  const ref=Object.freeze({
    schema_version:'0.5',
    ref_kind:'source',
    ref_id:link.evidenceRef,
    version:link.revision
  });
  return Object.freeze({
    status:assessment.status,
    reason:assessment.reason??null,
    sourceRef:ref,
    taskId,
    programId:link.programId,
    objectRef:link.objectRef,
    objectKind:link.objectKind,
    representation:link.representation,
    ownerRevision:link.ownerRevision,
    contentSha256:link.contentRef.slice('sha256:'.length),
    bytes:link.bytes,
    trustLevel:'external_unverified',
    authority:'OBSERVATION_ONLY',
    canUseCurrent:assessment.status==='CURRENT',
    // Only P01 can persist a reference and attach it to Result claim evidence_refs.
    requiresHostPersistence:true
  });
}

// P04 owns native Atlas selection and semantic workDigest. This only compares
// the owner identity and revision, never claims to verify P04's source file.
export function compareAgent03P04AtlasReference(link,p04,{workspaceId}={}){
  if(link?.schema!==TASK_SOURCE_SCHEMA||link.programId!=='ATLAS'||
      link.objectKind!=='atlas.work')return {status:'NOT_APPLICABLE'};
  const m=/^project\/([^/]+)\/work\/([^/]+)$/.exec(link.objectRef??'');
  const ref=ownerRef(p04?.ref??p04);
  const work=ownerRef(ref?.objectRef);
  const space=ownerRef(ref?.workspaceRef);
  if(!m||!ref||!work||!space||ref.ownerProduct!=='Atlas'||
      ref.purpose!=='personal_task_context'||
      space.ownerProduct!=='Personal Agent'||
      (workspaceId!==undefined&&workspaceId!==space.workspaceId))
    return {status:'P04_REFERENCE_INVALID'};
  if(work.projectId!==m[1]||work.workId!==m[2])
    return {status:'DIFFERENT_OWNER_OBJECT'};
  if(link.ownerRevision===null||String(link.ownerRevision)!==ref.revision)
    return {status:'OWNER_REVISION_CHANGED'};
  return {status:'OWNER_ID_AND_REVISION_MATCH',
    // Native source.path and workDigest are exclusively validated by P04.
    sourceBindingVerified:false,workDigestVerified:false};
}
