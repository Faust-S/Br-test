import assert from 'node:assert/strict';
import {empty,createProject,createItem,editItem,serialize} from '../../atlas-public/src/core.mjs';
import {createNativeAtlasPort} from '../../center-public/src/atlas-native.mjs';
import {readAtlasPortfolio,linkableAtlasWork,atlasWorkReadback} from '../../center-public/src/atlas-integration.mjs';
import {createHostSelectedSourcePort} from '../host-source.mjs';
import {captureSource,readPinnedSource} from '../source-read.mjs';

// This tests existing Center JS native host boundaries with the original Atlas core.
// It deliberately does not claim to run the installed Tauri GUI.
const project=createProject(empty(),{name:'U02 native selected source',type:'engineering'});
const created=createItem(project.state,project.id,{title:'Reopen source without writing'});
let snapshot=created.state,authorized=true,opens=0,writes=0;
const nativeInvoke=async (command,args)=>{
  if(command!=='center_atlas_open'){writes++;throw Error('UNEXPECTED_NATIVE_WRITE');}
  assert.equal(args.organizationId,'org-a');
  opens++;
  return {generation:'observed-generation',snapshot,needsConfirmation:false};
};
const nativePort=createNativeAtlasPort(nativeInvoke);
const adapter=(scopeId='org-a')=>createHostSelectedSourcePort({
  hostId:'admin-center',scopeId,sourceId:'atlas-project-snapshot',bindingId:'native-atlas-v1',
  readSelected:async()=>{
    if(!authorized)return {status:'AUTHORIZATION_REQUIRED'};
    const opened=await nativePort.open('org-a');
    return {status:'OK',bytes:new TextEncoder().encode(serialize(opened.snapshot))};
  }
});
const first=await captureSource(adapter());
assert.equal(first.status,'OK');
const initial=readAtlasPortfolio(Buffer.from(first.read.content.data,'base64').toString('utf8'));
const link=linkableAtlasWork(initial,project.id,created.id);
assert.equal(atlasWorkReadback(initial,link),'SOURCE_REVISION_MATCHES');
const saved=JSON.parse(JSON.stringify(first.pin));
const repeat=await readPinnedSource(adapter(),saved);
assert.equal(repeat.status,'OK');
assert.equal(atlasWorkReadback(
  readAtlasPortfolio(Buffer.from(repeat.content.data,'base64').toString('utf8')),link),
'SOURCE_REVISION_MATCHES');

snapshot=editItem(snapshot,project.id,created.id,{state:'done'});
const stale=await readPinnedSource(adapter(),saved);
assert.equal(stale.status,'REVISION_MISMATCH');
assert.equal(stale.content,null);
const changed=await captureSource(adapter());
assert.equal(changed.status,'OK');
const current=readAtlasPortfolio(Buffer.from(changed.read.content.data,'base64').toString('utf8'));
assert.equal(atlasWorkReadback(current,link),'SOURCE_REVISION_CHANGED');
const renewed=linkableAtlasWork(current,project.id,created.id);
assert.equal(atlasWorkReadback(current,renewed),'SOURCE_REVISION_MATCHES');
assert.equal(current.projects[0].workItems[0].state,'done');

const beforeOther=opens;
const foreign=await adapter('org-b').read(saved);
assert.equal(foreign.status,'REVISION_REQUIRED');
assert.equal(opens,beforeOther);
authorized=false;
const denied=await captureSource(adapter());
assert.equal(denied.status,'AUTHORIZATION_REQUIRED');
assert.equal(denied.pin,null);
assert.equal(opens,beforeOther);
assert.equal(writes,0);
console.log(JSON.stringify({schema:'IWXA_U02_CENTER_NATIVE_HOST_PORT/1',status:'PASS',
  actualCenterNativeJsPort:true,canonicalAtlasCore:true,hostOwnedNativeInvoke:true,
  crossOrgPinRefused:true,staleRefused:true,centerNewWorkLink:true,
  nativeOpenCalls:opens,nativeWrites:writes,installedGui:false}));
