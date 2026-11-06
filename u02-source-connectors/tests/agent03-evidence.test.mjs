import test from 'node:test';
import assert from 'node:assert/strict';
import {createHostSelectedSourcePort} from '../host-source.mjs';
import {bindSourceObject,linkTaskSource} from '../task-object.mjs';
import {projectAgent03SourceEvidence,compareAgent03P04AtlasReference}
  from '../consumers/agent03-evidence.mjs';

const identity={programId:'ATLAS',instanceRef:'desktop-a',sourceId:'projects',
  objectRef:'project/P1/work/W1',objectKind:'atlas.work',representation:'json'};
const nativeRef={ownerProduct:'Atlas',purpose:'personal_task_context',
  objectRef:{projectId:'P1',workId:'W1'},revision:'5',
  workspaceRef:{ownerProduct:'Personal Agent',workspaceId:'personal'},
  source:{mode:'USER_SELECTED_ATLAS_FILE',path:'C:/Projects/atlas.json'}};
const source=(readSelected,bindingId='atlas-file-a')=>createHostSelectedSourcePort({
  hostId:'personal-agent',scopeId:'personal',sourceId:'projects',bindingId,readSelected
});

test('Agent 0.3 source projection preserves strict P01 typed-ref without injecting U02 into Task or Result',async()=>{
  let snapshot={revision:5,projects:[{id:'P1',items:[{id:'W1',state:'open'}]}]},reads=0;
  const readSelected=async()=>{reads++;return {status:'OK',
    bytes:new TextEncoder().encode(JSON.stringify(snapshot))};};
  const port=await bindSourceObject(source(readSelected),identity);
  const created=await linkTaskSource(port,{taskRef:'TASK-A03-1',ownerRevision:5});
  assert.equal(created.status,'OK');
  assert.equal(reads,2);
  const saved=JSON.parse(JSON.stringify(created.link));
  const reopened=await bindSourceObject(source(readSelected),identity);
  const projected=await projectAgent03SourceEvidence(reopened,saved,{taskId:'TASK-A03-1'});
  assert.equal(projected.status,'CURRENT');assert.equal(reads,3);
  assert.deepEqual(projected.sourceRef,{schema_version:'0.5',ref_kind:'source',
    ref_id:saved.evidenceRef,version:saved.revision});
  assert.equal(projected.contentSha256,saved.contentRef.slice(7));
  assert.equal(projected.trustLevel,'external_unverified');
  assert.equal(projected.authority,'OBSERVATION_ONLY');
  assert.equal(projected.requiresHostPersistence,true);
  assert.equal(projected.canUseCurrent,true);
  assert.equal(JSON.stringify(projected).includes('content.data'),false);
  assert.equal('digest_sha256' in projected.sourceRef,false);
  assert.equal('digest_profile' in projected.sourceRef,false);

  snapshot={revision:6,projects:[{id:'P1',items:[{id:'W1',state:'done'}]}]};
  const stale=await projectAgent03SourceEvidence(reopened,saved,{taskId:'TASK-A03-1'});
  assert.equal(stale.status,'STALE');assert.equal(stale.canUseCurrent,false);
  assert.equal(stale.sourceRef.ref_id,projected.sourceRef.ref_id);
  assert.equal(stale.ownerRevision,5);
});

test('Agent 0.3 rejects foreign Task, mismatched pin and owner without accessing or rewriting Agent data',async()=>{
  let reads=0;
  const port=await bindSourceObject(source(async()=>{
    reads++;return {status:'OK',bytes:new TextEncoder().encode('source')};
  }),identity);
  const {link}=await linkTaskSource(port,{taskRef:'TASK-BOUND-1',ownerRevision:5});
  const original=reads;
  assert.deepEqual(await projectAgent03SourceEvidence(port,link,{taskId:'TASK-OTHER-2'}),
    {status:'TASK_MISMATCH',sourceRef:null,canUseCurrent:false});
  assert.equal(reads,original);
  const tampered={...link,objectRef:'project/P1/work/W2'};
  assert.deepEqual(await projectAgent03SourceEvidence(port,tampered,{taskId:'TASK-BOUND-1'}),
    {status:'SOURCE_MISMATCH',sourceRef:null,canUseCurrent:false});
  assert.equal(reads,original);
  const foreign=await bindSourceObject(source(async()=>{throw Error('never open')},'other-store'),identity);
  assert.equal((await projectAgent03SourceEvidence(foreign,link,{taskId:'TASK-BOUND-1'})).status,
    'SOURCE_MISMATCH');
  await assert.rejects(()=>projectAgent03SourceEvidence(port,link,{taskId:'INVALID'}),/AGENT03_TASK_ID_INVALID/);
});

test('Agent 0.3 unavailable owner yields UNKNOWN, not verified content or automatic Result acceptance',async()=>{
  let allowed=true;
  const readSelected=async()=>allowed?{status:'OK',bytes:new TextEncoder().encode('text')}:
    {status:'AUTHORIZATION_REQUIRED'};
  const port=await bindSourceObject(source(readSelected),identity);
  const {link}=await linkTaskSource(port,{taskRef:'TASK-OWNER-1',ownerRevision:5});
  allowed=false;
  const projection=await projectAgent03SourceEvidence(port,link,{taskId:'TASK-OWNER-1'});
  assert.equal(projection.status,'UNKNOWN');
  assert.equal(projection.reason,'AUTHORIZATION_REQUIRED');
  assert.equal(projection.canUseCurrent,false);
  assert.equal(projection.sourceRef.ref_id,link.evidenceRef);
});

test('Agent 0.3 P04 native Atlas owner reference compares ID and revision without asserting source path or workDigest',async()=>{
 const link={schema:'IWXA_U02_TASK_SOURCE_LINK/1.0.0',...identity,ownerRevision:5};
 const matched=compareAgent03P04AtlasReference(link,nativeRef,{workspaceId:'personal'});
 assert.deepEqual(matched,{status:'OWNER_ID_AND_REVISION_MATCH',
   sourceBindingVerified:false,workDigestVerified:false});
 assert.equal(compareAgent03P04AtlasReference(link,{...nativeRef,revision:'6'}).status,
   'OWNER_REVISION_CHANGED');
 assert.equal(compareAgent03P04AtlasReference(link,{...nativeRef,
   objectRef:{projectId:'P1',workId:'W2'}}).status,'DIFFERENT_OWNER_OBJECT');
 assert.equal(compareAgent03P04AtlasReference(link,nativeRef,{workspaceId:'AREA-other'}).status,
   'P04_REFERENCE_INVALID');
 assert.equal(compareAgent03P04AtlasReference({...link,objectKind:'browser.page'},
   nativeRef).status,'NOT_APPLICABLE');
});
