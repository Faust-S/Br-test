import assert from 'node:assert/strict';
import {test} from 'node:test';
import {observeU01Capabilities} from '../src/u01-bridge.mjs';
const source={schema:'IWXA_PROGRAM_CONNECTION/1.0.0',program_id:'JQ_CLI_V1',transport:'cli',
 status:'DECLARED',connected:true,connection_epoch:'fixtureepoch0000001',operations:[{name:'evaluate',declared:true,verified:true}]};
const receipt={observationRef:'host:test:42',observation:{schema:source.schema,program_id:source.program_id,
 transport:'cli',connection_epoch:'fixtureepoch0000001',status:'EXECUTED',executed:true,operation:'evaluate',output:'42'}};
const config={programId:'JQ_CLI_V1',bindings:[{operation:'evaluate',id:'iwxa.cli.evaluate',version:'1.0.0'}],
 receipts:{evaluate:receipt}};
const value=(s=source,c=config)=>observeU01Capabilities({capabilities:()=>s},c)[0];
test('a real U01-shaped operation requires both verified flag and host receipt',()=>{
 assert.equal(value().available,true);
 assert.equal(value(source,{...config,receipts:{}}).reason,'execution-receipt-missing');
 assert.equal(value({...source,operations:[{name:'evaluate',declared:true,verified:false}]}).reason,'operation-unverified');
 for(const patch of [{connected:false},{status:'CONNECTION_LOST'},{program_id:'WRONG_V1'},
   {schema:'IWXA_PROGRAM_CONNECTION/2.0.0'},{transport:'unsupported'}]){
   assert.equal(value({...source,...patch}).available,false);
 }
 assert.equal(value({...source,operations:[]}).reason,'operation-unsupported');
 assert.equal(value({...source,operations:[source.operations[0],source.operations[0]]}).reason,'source-unavailable');
 assert.equal(value({...source,operations:[{name:'evaluate',declared:false,verified:true}]}).reason,'operation-unsupported');
 for(const o of [{...receipt.observation,program_id:'YQ_CLI_V1'},
   {...receipt.observation,operation:'wrong'},
   {...receipt.observation,status:'EXECUTION_FAILED'},
   {...receipt.observation,executed:false}]){
   assert.equal(value(source,{...config,receipts:{evaluate:{...receipt,observation:o}}}).available,false);
 }
});
test('invalid configuration rejected before querying source',()=>{
 let hits=0;
 const connection={capabilities:()=>{hits++;return source;}};
 for(const invalid of [{...config,programId:'WRONG\n'},
    {...config,bindings:[{...config.bindings[0],id:'iwxa.cli.evaluate\n'}]},
    {...config,bindings:[config.bindings[0],config.bindings[0]]},
    {...config,receipts:null}]){
  assert.throws(()=>observeU01Capabilities(connection,invalid),/U03_U01_BINDINGS_INVALID/);
 }
 assert.equal(hits,0);
});
test('no source or failure cannot yield available capability',()=>{
 assert.equal(observeU01Capabilities({capabilities:()=>{throw Error('gone')}},config)[0].available,false);
});

test('replayed receipts fail in a new connection even when the operation becomes verified again',()=>{
 const later={...source,connection_epoch:'fixtureepoch0000002'};
 assert.equal(value(later).reason,'execution-receipt-missing');
 assert.equal(value({...source,connection_epoch:undefined}).reason,'connection-unscoped');
 assert.equal(value({...source,connection_epoch:'x'}).reason,'connection-unscoped');
 const newer={...receipt,observation:{...receipt.observation,connection_epoch:later.connection_epoch}};
 assert.equal(value(later,{...config,receipts:{evaluate:newer}}).available,true);
});
