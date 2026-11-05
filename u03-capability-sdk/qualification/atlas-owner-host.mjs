// External consumer of installed U03 SDK + unchanged public Atlas project host port.
// The Atlas owner owns project persistence, refs and authorization.
import assert from 'node:assert/strict';
import {readFileSync, writeFileSync, renameSync, existsSync} from 'node:fs';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {assess, createModuleAdapter} from '@iwxa/capability-sdk';

const [phase, snapshotPath, referencePath, atlasRoot] = process.argv.slice(2);
assert.ok(['create','reopen','change'].includes(phase),'U03_ATLAS_PHASE_INVALID');
assert.ok([snapshotPath,referencePath,atlasRoot].every(Boolean),'U03_ATLAS_ARGS_REQUIRED');
const atlasModule = file => pathToFileURL(resolve(atlasRoot,'src',file)).href;
const {empty,createProject,createItem,editProject,serialize,deserialize} =
  await import(atlasModule('core.mjs'));
const {createAtlasSavedRef,createAtlasProjectReadPort,createAtlasCenterHostAdapter,
  createAtlasAgent03HostAdapter} = await import(atlasModule('project-host-port.mjs'));

const persist = state => {
  const serialized=serialize(state);
  writeFileSync(snapshotPath+'.tmp',serialized,{encoding:'utf8'});
  renameSync(snapshotPath+'.tmp',snapshotPath);
  const saved=deserialize(readFileSync(snapshotPath,'utf8'));
  assert.equal(serialize(saved),serialized,'U03_ATLAS_CANONICAL_READBACK_MISMATCH');
  return saved;
};
let state, workRef;
if(phase==='create'){
  assert.equal(existsSync(snapshotPath),false,'U03_ATLAS_INITIAL_STATE_NOT_EMPTY');
  const p=createProject(empty(),{name:'U03 Actual Atlas Host',type:'engineering'});
  const w=createItem(p.state,p.id,{title:'Validate source interoperability',priority:'high'});
  state=persist(w.state);
  assert.throws(()=>createAtlasSavedRef(state,{projectId:p.id,workId:w.id},
    {persisted:false,dirty:false}),/ATLAS_REF_REQUIRES_SAVED_REVISION/);
  workRef=createAtlasSavedRef(state,{projectId:p.id,workId:w.id},
    {persisted:true,dirty:false});
  writeFileSync(referencePath,JSON.stringify(workRef),{encoding:'utf8'});
}else{
  assert.equal(existsSync(snapshotPath),true,'U03_ATLAS_MISSING_PERSISTED_STATE');
  state=deserialize(readFileSync(snapshotPath,'utf8'));
  workRef=JSON.parse(readFileSync(referencePath,'utf8'));
}
const authorize=({context})=>
  context?.workspaceId==='u03-workspace' || context?.organizationId==='u03-organization';
const port=createAtlasProjectReadPort({
  openSnapshot:async()=>deserialize(readFileSync(snapshotPath,'utf8')),authorize,
});
const agent=createAtlasAgent03HostAdapter(port);
const center=createAtlasCenterHostAdapter(port);
const moduleDescriptor={schema:'IWXA_MODULE_DESCRIPTOR/1.0.0',
  id:'iwxa.u03.atlas.saved-work',version:'0.1.0',provides:[],
  requires:[{id:'iwxa.atlas.saved-work.read',range:'=0.1.0'}],
  hostCompatibility:[{hostId:'iwxa.atlas.project-read-port',range:'=0.1.0'}]};
const host={hostId:'iwxa.atlas.project-read-port',version:'0.1.0'};
let attached=0,detached=0,denied=0;
let currentRef=workRef;
const readStatus=async(ref,origin='agent')=>{
  try{
    const record=origin==='center'
      ?await center.previewAssignment(ref,{organizationId:'u03-organization'})
      :await agent.selectedWork(ref,{workspaceId:'u03-workspace'});
    assert.equal(record.ref.revision,ref.revision);
    assert.equal(record.ref.projectId,ref.projectId);
    assert.equal(record.ref.workId,ref.workId);
    return {schema:'IWXA_CAPABILITY_STATUS/1.0.0',
      id:'iwxa.atlas.saved-work.read',available:true,version:'0.1.0',
      observationRef:['atlas-saved-work',record.ref.projectId,record.ref.workId,
        record.ref.revision].join(':')};
  }catch(error){
    return {schema:'IWXA_CAPABILITY_STATUS/1.0.0',
      id:'iwxa.atlas.saved-work.read',available:false,
      reason:String(error?.code??error?.message??'ATLAS_READ_UNAVAILABLE').slice(0,256)};
  }
};
const adapter=createModuleAdapter(moduleDescriptor,{
  attach:async()=>{
    // The REAL Atlas owner port is rechecked by the host hook, not by U03.
    const current=await port.work(currentRef,{workspaceId:'u03-workspace'});
    assert.equal(current.ref.revision,currentRef.revision);
    attached++;
    return {attached:true,bindingRef:'atlas-host-binding:'+attached};
  },
  detach:async ref=>{
    assert.equal(ref,'atlas-host-binding:'+attached);
    detached++;
    return {detached:true};
  },
});
const tryAttach=async(ref,origin='agent')=>{
  currentRef=ref;
  const observation=await readStatus(ref,origin);
  const result=await adapter.attach(host,[observation]);
  return {observation,result,compatible:assess(moduleDescriptor,host,[observation]).compatible};
};

if(phase==='change'){
  const amended=editProject(state,workRef.projectId,
    {name:'U03 Actual Atlas Host — edited'});
  state=persist(amended);
  const stale=await tryAttach(workRef);
  assert.equal(stale.compatible,false,'stale Atlas revision must not be available');
  assert.equal(stale.result.attached,false,'U03 must refuse outdated Atlas revision');
  assert.equal(stale.observation.reason,'ATLAS_SOURCE_REVISION_CHANGED');
  assert.equal(attached,0,'stale work must never reach host attach');
  const refreshed=createAtlasSavedRef(state,
    {projectId:workRef.projectId,workId:workRef.workId},
    {persisted:true,dirty:false});
  assert.notEqual(refreshed.revision,workRef.revision);
  const value=await tryAttach(refreshed,'center');
  assert.equal(value.compatible,true);
  assert.equal(value.result.attached,true);
  assert.equal((await adapter.detach(value.result.hostReceipt.bindingRef)).detached,true);
  writeFileSync(referencePath,JSON.stringify(refreshed),{encoding:'utf8'});
  assert.equal(attached,1);
}else{
  const success=await tryAttach(workRef,phase==='reopen'?'center':'agent');
  assert.equal(success.compatible,true);
  assert.equal(success.result.attached,true);
  assert.equal((await adapter.detach(success.result.hostReceipt.bindingRef)).detached,true);
  assert.equal(attached,1);
  if(phase==='reopen'){
    assert.equal(state.projects.length,1);
    assert.equal(state.projects[0].items[0].title,'Validate source interoperability');
  }
}
await assert.rejects(
  port.work(currentRef,{workspaceId:'unauthorized-workspace'}),
  /ATLAS_HOST_AUTHORIZATION_DENIED/);
denied++;
await assert.rejects(
  agent.selectedWork(currentRef,{}),
  /ATLAS_WORKSPACE_CONTEXT_REQUIRED/);
denied++;
await assert.rejects(
  center.previewAssignment(currentRef,{}),
  /ATLAS_ORGANIZATION_CONTEXT_REQUIRED/);
denied++;
assert.equal(attached,1);
assert.equal(detached,1);
assert.equal(denied,3);
const saved=deserialize(readFileSync(snapshotPath,'utf8'));
assert.equal(saved.projects.length,1);
assert.equal(saved.projects[0].items.length,1);
console.log(JSON.stringify({schema:'IWXA_U03_ATLAS_SOURCE_CONSUMER/1',
  phase,status:'PASS',source:'atlas-public-pinned-commit',
  canonicalSchema:saved.schema,revision:saved.revision,workRefCurrent:true,
  installedSdk:true,ownerPortUnmodified:true,hostAuthorizedReads:true,
  staleRevisionRejected:phase==='change',
  ownerDeniedUnauthorized:true,hostAttachDelegated:true,
  privateProductModified:false}));
