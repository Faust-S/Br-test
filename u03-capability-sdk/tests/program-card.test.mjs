import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import {projectProgramCard} from '../src/program-card.mjs';

const cases=JSON.parse(readFileSync(
  new URL('../fixtures/program-card-scenarios.json',import.meta.url),'utf8'));
const derive=entry=>projectProgramCard(entry.args);
for(const entry of cases){
 test('Programs Center independent capability projection: '+entry.case,()=>{
  const projected=derive(entry),byId=Object.fromEntries(projected.capabilities.map(x=>[x.id,x]));
  assert.equal(projected.programId,entry.args.programId);
  assert.equal(projected.highestVerified,entry.args.ui.highestVerified);
  assert.equal(projected.capabilities.length,entry.args.channels.length);
  for(const [id,expected] of Object.entries(entry.expected)){
    assert.equal(byId[id]?.state,expected,id+' state');
    assert.equal(typeof byId[id].version,'string');
    assert.equal(typeof byId[id].owner,'string');
    assert.equal(typeof byId[id].level,'string');
    assert.ok(['DECLARED','INTEGRATED','AVAILABLE','EXECUTABLE','BLOCKED'].includes(byId[id].state));
  }
  for(const [id,values] of Object.entries(entry.checks??{}))
    for(const [key,value] of Object.entries(values))
      assert.deepEqual(byId[id]?.[key],value,id+' '+key);
  assert.equal('grants' in projected,false);
  assert.equal('invoke' in projected,false);
 });
}
test('window observation does not verify embedding, shared live surface or operations',()=>{
 const entry=cases.find(c=>c.case==='obsidian_native_window');
 const by=Object.fromEntries(derive(entry).capabilities.map(x=>[x.kind,x]));
 assert.equal(by.LINKED_WINDOW.state,'DECLARED');
 assert.equal(by.LINKED_WINDOW.target,true);
 assert.equal(by.EMBEDDED_VIEW.state,'BLOCKED');
 assert.equal(by.SHARED_LIVE_SURFACE.state,'BLOCKED');
 assert.equal(by.OPERATION.executionConfirmed,false);
});
test('installed program never qualifies operations or a future Browser shared surface',()=>{
 const entry=cases.find(c=>c.case==='browser_shared_live_target');
 const by=Object.fromEntries(derive(entry).capabilities.map(x=>[x.kind,x]));
 assert.equal(by.DISCOVERY.state,'AVAILABLE');
 assert.equal(by.OPERATION.state,'BLOCKED');
 assert.equal(by.SHARED_LIVE_SURFACE.state,'DECLARED');
 assert.equal(by.SHARED_LIVE_SURFACE.verified,false);
});
test('stale U01 receipts and lost UI window cannot appear as current execution',()=>{
 const original=cases.find(c=>c.case==='atlas_embedded_ui').args;
 const stale=structuredClone(original);
 stale.u01.capabilities.connection_epoch='epoch0000000000002';
 const by=Object.fromEntries(projectProgramCard(stale).capabilities.map(x=>[x.kind,x]));
 assert.equal(by.OPERATION.state,'BLOCKED');
 assert.equal(by.OPERATION.reason,'execution-receipt-missing');
 assert.equal(by.EMBEDDED_VIEW.state,'DECLARED');
 const linked=structuredClone(cases.find(c=>c.case==='obsidian_native_window').args);
 linked.presentation.observations.LINKED_WINDOW={schema:'IWXA_PRESENTATION_BRIDGE/1.0.0',status:'WINDOW_NOT_FOUND',binding:{appRef:'OBSIDIAN_NATIVE_V1',hostRef:'agent-host',bindingRef:'window:lost',windowRef:'native:lost',processId:12}};
 const by2=Object.fromEntries(projectProgramCard(linked).capabilities.map(x=>[x.kind,x]));
 assert.equal(by2.LINKED_WINDOW.state,'BLOCKED');
 assert.equal(by2.LINKED_WINDOW.verified,false);
});
test('an advertised surface is not a mounted live surface',()=>{
 const input=structuredClone(cases.find(c=>c.case==='atlas_embedded_ui').args);
 input.presentation.observations.EMBEDDED_VIEW={schema:'IWXA_PRESENTATION_SURFACE/1.0.0',status:'SURFACE_MOUNTED',binding:{appRef:'ATLAS_UI_V1',hostRef:'agent-host',bindingRef:'surface:1',mode:'EMBEDDED_VIEW',official:true,verified:true,existingRuntime:true,spawned:false,mounted:true,mountRef:'mount:1',presentation:{official:true}}};
 const mounted=Object.fromEntries(projectProgramCard(input).capabilities.map(x=>[x.kind,x]));
 assert.equal(mounted.EMBEDDED_VIEW.state,'INTEGRATED');
 assert.equal(mounted.EMBEDDED_VIEW.reason,'SURFACE_LIVE_RECHECK_REQUIRED');
 input.presentation.observations.EMBEDDED_VIEW.binding.official=false;
 const bad=Object.fromEntries(projectProgramCard(input).capabilities.map(x=>[x.kind,x]));
 assert.equal(bad.EMBEDDED_VIEW.state,'BLOCKED');
 assert.equal(bad.EMBEDDED_VIEW.reason,'OFFICIAL_SURFACE_PROOF_INVALID');
});
test('the existing I0-I5 source is not collapsed to highestVerified',()=>{
 const input=structuredClone(cases.find(c=>c.case==='atlas_embedded_ui').args);
 const original=projectProgramCard(input);
 assert.equal(original.highestVerified,'I2');
 assert.equal(original.capabilities.find(x=>x.kind==='SHARED_LIVE_SURFACE').state,'BLOCKED');
 assert.equal(original.capabilities.find(x=>x.kind==='EMBEDDED_VIEW').state,'DECLARED');
 assert.equal(original.levels[4].verified,false);
 assert.equal(original.levels[3].verified,false);
});
test('incorrect cross-app binding and incompatible versions are blocked',()=>{
 const input=structuredClone(cases.find(c=>c.case==='obsidian_native_window').args);
 input.presentation.observations.LINKED_WINDOW={schema:'IWXA_PRESENTATION_BRIDGE/1.0.0',status:'WINDOW_FOCUSED',focused:true,binding:{appRef:'WRONG_APP',hostRef:'agent-host',bindingRef:'window:1',windowRef:'native:1',processId:12}};
 const rows=projectProgramCard(input).capabilities;
 assert.equal(rows.find(x=>x.kind==='LINKED_WINDOW').reason,'PRESENTATION_BINDING_MISMATCH');
 const atlas=structuredClone(cases.find(c=>c.case==='atlas_embedded_ui').args);
 atlas.statuses[0].version='0.9.0';
 assert.equal(projectProgramCard(atlas).capabilities.find(x=>x.kind==='DATA').reason,
  'CAPABILITY_VERSION_MISMATCH');
});
test('invalid configuration fails before projection',()=>{
 const sample=structuredClone(cases[0].args);
 assert.throws(()=>projectProgramCard({...sample,channels:[]}),/U03_PROGRAM_CARD_INPUT_INVALID/);
 const invalid=structuredClone(sample);
 invalid.statuses.push(invalid.statuses[0]);
 assert.throws(()=>projectProgramCard(invalid),/U03_PROGRAM_CARD_INPUT_INVALID/);
});

test('valid host-owned U04 window proof promotes linked mode only',()=>{
 const input=structuredClone(cases.find(c=>c.case==='obsidian_native_window').args);
 input.presentation.observations.LINKED_WINDOW={schema:'IWXA_PRESENTATION_BRIDGE/1.0.0',
 status:'WINDOW_FOCUSED',focused:true,binding:{appRef:'OBSIDIAN_NATIVE_V1',
 hostRef:'agent-host',bindingRef:'window:linked',processId:1234,windowRef:'native:window:1234'}};
 const result=Object.fromEntries(projectProgramCard(input).capabilities.map(x=>[x.kind,x]));
 assert.equal(result.LINKED_WINDOW.state,'AVAILABLE');
 assert.equal(result.EMBEDDED_VIEW.state,'BLOCKED');
 assert.equal(result.SHARED_LIVE_SURFACE.state,'BLOCKED');
 assert.equal(result.OPERATION.executionConfirmed,false);
 assert.equal(projectProgramCard(input).highestVerified,'I1','only U01 owns I0-I5 level snapshot');
});
test('official U04 live surface promotes only its actual presentation mode',()=>{
 const input=structuredClone(cases.find(c=>c.case==='atlas_embedded_ui').args);
 input.presentation.observations.EMBEDDED_VIEW={
  schema:'IWXA_PRESENTATION_SURFACE/1.0.0',status:'SURFACE_LIVE',
  binding:{appRef:'ATLAS_UI_V1',hostRef:'agent-host',bindingRef:'surface:atlas-live',
   mode:'EMBEDDED_VIEW',official:true,verified:true,existingRuntime:true,
   spawned:false,mounted:true,mountRef:'host:mount:1',
   presentation:{kind:'WEB_URL',official:true}}
 };
 const card=projectProgramCard(input);
 const result=Object.fromEntries(card.capabilities.map(x=>[x.kind,x]));
 assert.equal(result.EMBEDDED_VIEW.state,'AVAILABLE');
 assert.equal(result.EMBEDDED_VIEW.observationSource,'IWXA_PRESENTATION_SURFACE/1.0.0');
 assert.equal(result.EMBEDDED_VIEW.observationRef,'surface:atlas-live');
 assert.equal(result.SHARED_LIVE_SURFACE.state,'BLOCKED');
 assert.equal(result.LINKED_WINDOW.state,'BLOCKED');
 assert.equal(card.highestVerified,'I2');
});
