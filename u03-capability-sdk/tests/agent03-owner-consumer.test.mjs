import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import {projectProgramCard} from '../src/program-card.mjs';

const cases=JSON.parse(readFileSync(new URL('../fixtures/agent03-owner-projection.json',import.meta.url),'utf8'));
for(const entry of cases){
  test('Agent 0.3 existing Core snapshot: '+entry.case,()=>{
    const result=projectProgramCard(entry.args);
    const operation=result.capabilities.find(x=>x.kind==='OPERATION');
    for(const [key,value] of Object.entries(entry.expected))
      assert.deepEqual(operation[key],value,entry.case+' '+key);
    assert.equal(result.capabilities.length,entry.args.descriptor.provides.length);
    assert.equal('execution_authority_granted' in result,false);
    assert.equal('grants' in result,false);
    assert.equal('invoke' in result,false);
  });
}
test('Agent snapshot cannot mix independently sourced raw U01 observations',()=>{
 const entry=structuredClone(cases[0].args);
 entry.u01.capabilities=entry.u01.ownerProjection.source;
 assert.throws(()=>projectProgramCard(entry),/U03_PROGRAM_CARD_INPUT_INVALID/);
 delete entry.u01.capabilities;
 entry.u01.receipts={evaluate:{}};
 assert.throws(()=>projectProgramCard(entry),/U03_PROGRAM_CARD_INPUT_INVALID/);
});
test('Agent snapshot requires Core authority=FALSE and exact source identity',()=>{
 const entry=structuredClone(cases[0].args);
 entry.u01.ownerProjection.execution_authority_granted=true;
 assert.throws(()=>projectProgramCard(entry),/U03_PROGRAM_CARD_INPUT_INVALID/);
 entry.u01.ownerProjection.execution_authority_granted=false;
 entry.u01.ownerProjection.program_id='OTHER_PROGRAM_V1';
 assert.throws(()=>projectProgramCard(entry),/U03_PROGRAM_CARD_INPUT_INVALID/);
});
test('mismatched operation or U03 status cannot promote availability',()=>{
 const entry=structuredClone(cases[0].args);
 entry.u01.ownerProjection.operations[0].capability_id='wrong.capability';
 assert.equal(projectProgramCard(entry).capabilities.find(x=>x.kind==='OPERATION').reason,
  'OWNER_OPERATION_MISMATCH');
 entry.u01.ownerProjection.operations[0].capability_id='iwxa.program.operation.execute';
 entry.u01.ownerProjection.operations[0].execution_verified=false;
 assert.equal(projectProgramCard(entry).capabilities.find(x=>x.kind==='OPERATION').reason,
  'OWNER_OBSERVATION_INCONSISTENT');
});
test('Agent snapshot projection is pure and does not modify current Core source',()=>{
 const entry=structuredClone(cases[1].args),before=JSON.stringify(entry);
 const first=projectProgramCard(entry);
 const second=projectProgramCard(entry);
 assert.deepEqual(first,second);
 assert.equal(JSON.stringify(entry),before);
});

test('Agent 0.5 owner readback rejects malformed U01 snapshots without a second probe',()=>{
 const sourceCase=cases.find(c=>c.case==='agent03_verified_execution').args;
 for(const change of [
  source=>{source.transport='not-a-u01-transport';},
  source=>{source.operations.push({name:'other',declared:'yes',verified:false});},
  source=>{source.operations.push({name:'other',declared:true,verified:false});
    source.operations.push({name:'other',declared:true,verified:false});},
  source=>{source.operations[0].verified='true';},
 ]){
  const snapshot=structuredClone(sourceCase);
  change(snapshot.u01.ownerProjection.source);
  const inputCopy=structuredClone(snapshot);
  const view=projectProgramCard(snapshot);
  const operation=view.capabilities.find(x=>x.kind==='OPERATION');
  assert.equal(operation.state,'BLOCKED');
  assert.equal(operation.reason,'source-unavailable');
  assert.equal(operation.executionConfirmed,false);
  assert.equal(operation.verified,false);
  assert.equal(operation.canOfferInvocation,false);
  assert.equal(operation.observationRef,null);
  assert.deepEqual(snapshot,inputCopy,'read-only UI projection may not modify Core evidence');
 }
});
