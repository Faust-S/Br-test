import assert from 'node:assert/strict';
import {test} from 'node:test';
import {createProductHostPort} from '../../universal-install/host-bindings/product-hosts.mjs';
import {observeU01CapabilitiesAsync,observeU01Capabilities} from '../src/u01-bridge.mjs';
import {bindModuleToU01Host} from '../src/host-binding.mjs';

const schema='IWXA_PROGRAM_CONNECTION/1.0.0';
const epochA='hostepoch000000001';
const epochB='hostepoch000000002';
const desc={schema:'IWXA_MODULE_DESCRIPTOR/1.0.0',id:'iwxa.u03.host.demo',
 version:'0.1.0',provides:[],requires:[{id:'iwxa.cli.evaluate',range:'=1.0.0'}],
 hostCompatibility:[{hostId:'iwxa.host.admin-center',range:'=0.1.0'}]};
const cfg={programId:'JQ_CLI_V1',bindings:[
 {operation:'evaluate',id:'iwxa.cli.evaluate',version:'1.0.0'}]};
const receipt=(epoch)=>({observationRef:'host:receipt:'+epoch,observation:{
 schema,program_id:'JQ_CLI_V1',transport:'cli',status:'EXECUTED',
 connection_epoch:epoch,operation:'evaluate',executed:true,output:'42'}});
const source=epoch=>({schema,program_id:'JQ_CLI_V1',transport:'cli',status:'DECLARED',
 connected:true,connection_epoch:epoch,
 operations:[{name:'evaluate',declared:true,verified:true}]});

test('async U01 product host port is consumed without a new executor',async()=>{
 let calls=0,epoch=epochA,approved=true,attached=0,detached=0,broken=false;
 const port=createProductHostPort('admin-center',async({schema:wire,host,operation})=>{
   assert.equal(wire,schema);assert.equal(host,'admin-center');
   if(operation!=='capabilities')throw Error('no auto-connect or invoke');
   calls++;
   if(broken)throw Error('host unavailable');
   return source(epoch);
 });
 const bindings={...cfg,receipts:{evaluate:receipt(epochA)}};
 const bridge=bindModuleToU01Host({
   descriptor:desc,host:{hostId:'iwxa.host.admin-center',version:'0.1.0'},
   port,getBindings:()=>bindings,
   hooks:{
     attach:async()=>{
       if(!approved)return {attached:false,reason:'host-denied'};
       attached++;
       return {attached:true,bindingRef:'host:'+attached};
     },
     detach:async ref=>{detached++;return {detached:true,bindingRef:ref};},
   },
 });
 assert.equal(bridge.describe().id,desc.id);
 assert.equal((await bridge.assess()).compatible,true);
 const first=await bridge.attach();
 assert.equal(first.attached,true);
 assert.equal(first.hostReceipt.bindingRef,'host:1');
 assert.equal('grants' in first,false);
 approved=false;
 assert.equal((await bridge.attach()).attached,false,'host hook retains authority');
 assert.equal(attached,1);
 approved=true;
 assert.equal((await bridge.detach('host:1')).detached,true);
 assert.equal(detached,1);
 epoch=epochB;
 const stale=await bridge.attach();
 assert.equal(stale.attached,false);
 assert.deepEqual(stale.assessment.reasons,['U03_CAPABILITY_UNAVAILABLE:iwxa.cli.evaluate']);
 assert.equal(attached,1);
 bindings.receipts={evaluate:receipt(epochB)};
 assert.equal((await bridge.attach()).attached,true);
 broken=true;
 assert.equal((await bridge.attach()).attached,false,'transport failure revokes projection');
 assert.equal(calls,8);
 assert.equal(attached,2);
});
test('async bridge validates mapping first, rejects malformed observations and versions',async()=>{
 let queries=0;
 const port={capabilities:async()=>{queries++;return source(epochA);}};
 assert.equal((await observeU01CapabilitiesAsync(port,{...cfg,receipts:{evaluate:receipt(epochA)}}))[0].available,true);
 assert.equal((await observeU01CapabilitiesAsync({capabilities:()=>{throw Error('offline');}},cfg))[0].available,false);
 assert.equal(observeU01Capabilities({capabilities:()=>Promise.resolve(source(epochA))},cfg)[0].available,false);
 await assert.rejects(observeU01CapabilitiesAsync(port,{...cfg,programId:'bad\n'}),/U03_U01_BINDINGS_INVALID/);
 assert.equal(queries,1,'invalid config must not reach host');
 const bridge=bindModuleToU01Host({descriptor:desc,
   host:{hostId:'iwxa.host.admin-center',version:'0.2.0'},port,
   getBindings:()=>({...cfg,receipts:{evaluate:receipt(epochA)}}),
   hooks:{attach:()=>{throw Error('must not call');},detach:()=>null}});
 assert.deepEqual((await bridge.assess()).reasons,['U03_HOST_VERSION_INCOMPATIBLE']);
 assert.equal((await bridge.attach()).attached,false);
 assert.equal(queries,3);
 assert.throws(()=>bindModuleToU01Host({descriptor:desc,port:{},getBindings:()=>cfg,
   hooks:{attach:()=>null,detach:()=>null}}),/U03_HOST_BINDING_PORT_REQUIRED/);
});

test('in-flight attach rolls back after host epoch changes',async()=>{
 let epoch=epochA,release,started;
 const entered=new Promise(resolve=>{started=resolve;});
 const gate=new Promise(resolve=>{release=resolve;});
 const refs=[];
 const port=createProductHostPort('admin-center',async({operation})=>{
   assert.equal(operation,'capabilities');
   return source(epoch);
 });
 const bridge=bindModuleToU01Host({
   descriptor:desc,host:{hostId:'iwxa.host.admin-center',version:'0.1.0'},
   port,getBindings:()=>({...cfg,receipts:{evaluate:receipt(epochA)}}),
   hooks:{
     attach:async()=>{started();await gate;return {attached:true,bindingRef:'host:in-flight'};},
     detach:async ref=>{refs.push(ref);return {detached:true};},
   },
 });
 const pending=bridge.attach();
 await entered;
 epoch=epochB;
 release();
 const result=await pending;
 assert.equal(result.attached,false);
 assert.deepEqual(result.assessment.reasons,['U03_CAPABILITY_UNAVAILABLE:iwxa.cli.evaluate']);
 assert.equal(result.rollback,'DETACHED');
 assert.equal(result.hostState,'DETACHED');
 assert.deepEqual(refs,['host:in-flight']);
 assert.equal(result.hostReceipt.bindingRef,'host:in-flight');
});

test('compatible but changed capability receipt also rolls back in-flight attach',async()=>{
 let current=epochA;
 let release,started;
 const entered=new Promise(resolve=>{started=resolve;});
 const gate=new Promise(resolve=>{release=resolve;});
 const refs=[];
 const bridge=bindModuleToU01Host({
  descriptor:desc,host:{hostId:'iwxa.host.admin-center',version:'0.1.0'},
  port:{capabilities:async()=>source(current)},
  getBindings:()=>({...cfg,receipts:{evaluate:receipt(current)}}),
  hooks:{
    attach:async()=>{started();await gate;return {attached:true,bindingRef:'host:epochA'};},
    detach:async ref=>{refs.push(ref);return {detached:true};},
  },
 });
 const pending=bridge.attach();
 await entered;
 current=epochB;
 release();
 const result=await pending;
 assert.equal(result.attached,false);
 assert.deepEqual(result.assessment.reasons,['U03_HOST_OBSERVATION_CHANGED']);
 assert.equal(result.rollback,'DETACHED');
 assert.deepEqual(refs,['host:epochA']);
});

test('unconfirmed rollback is surfaced, never reported as safe detach',async()=>{
 for(const mode of ['ref-missing','host-denied','host-exception']){
  let current=epochA,release,started;
  const entered=new Promise(resolve=>{started=resolve;});
  const gate=new Promise(resolve=>{release=resolve;});
  let calls=0;
  const bridge=bindModuleToU01Host({
    descriptor:desc,host:{hostId:'iwxa.host.admin-center',version:'0.1.0'},
    port:{capabilities:async()=>source(current)},
    getBindings:()=>({...cfg,receipts:{evaluate:receipt(epochA)}}),
    hooks:{
      attach:async()=>{started();await gate;return mode==='ref-missing'
        ?{attached:true}:{attached:true,bindingRef:'host:uncertain'};},
      detach:async()=>{calls++;if(mode==='host-exception')throw Error('host disconnected');
        return {detached:false};},
    },
  });
  const pending=bridge.attach();
  await entered;
  current=epochB;
  release();
  const result=await pending;
  assert.equal(result.attached,false);
  assert.equal(result.rollback,'UNCONFIRMED');
  assert.equal(result.hostState,'UNKNOWN');
  assert.equal(calls,mode==='ref-missing'?0:1);
 }
});

test('post-attach observation errors trigger host-owned detach, not false success',async()=>{
 let calls=0,detached=0;
 const bridge=bindModuleToU01Host({
   descriptor:desc,host:{hostId:'iwxa.host.admin-center',version:'0.1.0'},
   port:{capabilities:async()=>source(epochA)},
   getBindings:()=>{calls++;if(calls===2)throw Error('no receipt cache');
     return {...cfg,receipts:{evaluate:receipt(epochA)}};},
   hooks:{
      attach:async()=>({attached:true,bindingRef:'host:lost'}),
      detach:async()=>{detached++;return {detached:true};},
   },
 });
 const result=await bridge.attach();
 assert.equal(result.attached,false);
 assert.deepEqual(result.assessment.reasons,['U03_HOST_OBSERVATION_LOST']);
 assert.equal(result.rollback,'DETACHED');
 assert.equal(detached,1);
});

test('concurrent async attaches refuse the second call without a duplicate host action', async()=>{
  let unblock,entered,attached=0,detached=0;
  const start=new Promise(resolve=>{entered=resolve;});
  const gate=new Promise(resolve=>{unblock=resolve;});
  const bridge=bindModuleToU01Host({
    descriptor:desc,host:{hostId:'iwxa.host.admin-center',version:'0.1.0'},
    port:{capabilities:async()=>source(epochA)},
    getBindings:()=>({...cfg,receipts:{evaluate:receipt(epochA)}}),
    hooks:{
      attach:async()=>{
        attached++;
        entered();
        await gate;
        return {attached:true,bindingRef:'host:concurrent'};
      },
      detach:async()=>{detached++;return {detached:true};},
    },
  });
  const first=bridge.attach();
  await start;
  const second=await bridge.attach();
  assert.equal(second.attached,false);
  assert.deepEqual(second.assessment.reasons,['U03_HOST_ATTACH_IN_PROGRESS']);
  assert.equal(second.hostReceipt,null);
  assert.equal(attached,1);
  unblock();
  assert.equal((await first).attached,true);
  assert.equal((await bridge.attach()).attached,true);
  assert.equal(attached,2);
  assert.equal(detached,0);
});

test('positive host receipt without a usable bindingRef is never declared attached',async()=>{
  for(const missingRef of [undefined,null,'','  ',17]){
    let reads=0,detaches=0;
    const bridge=bindModuleToU01Host({
      descriptor:desc,host:{hostId:'iwxa.host.admin-center',version:'0.1.0'},
      port:{capabilities:async()=>{reads++;return source(epochA);}},
      getBindings:()=>({...cfg,receipts:{evaluate:receipt(epochA)}}),
      hooks:{
        attach:async()=>({attached:true,bindingRef:missingRef}),
        detach:async()=>{detaches++;return {detached:true};},
      },
    });
    const result=await bridge.attach();
    assert.equal(result.attached,false);
    assert.deepEqual(result.assessment.reasons,['U03_HOST_BINDING_REF_MISSING']);
    assert.equal(result.rollback,'UNCONFIRMED');
    assert.equal(result.hostState,'UNKNOWN');
    assert.equal(reads,1);
    assert.equal(detaches,0);
  }
});

test('host denial preserves owner decision and leaves in-flight guard reusable',async()=>{
  let approved=false,attempts=0;
  const bridge=bindModuleToU01Host({
    descriptor:desc,host:{hostId:'iwxa.host.admin-center',version:'0.1.0'},
    port:{capabilities:async()=>source(epochA)},
    getBindings:()=>({...cfg,receipts:{evaluate:receipt(epochA)}}),
    hooks:{
      attach:async()=>{attempts++;return approved
        ?{attached:true,bindingRef:'host:approved'}:{attached:false,reason:'owner-denied'};},
      detach:async()=>({detached:true}),
    },
  });
  const denied=await bridge.attach();
  assert.equal(denied.attached,false);
  assert.equal(denied.hostReceipt.reason,'owner-denied');
  assert.equal('rollback' in denied,false);
  approved=true;
  assert.equal((await bridge.attach()).attached,true);
  assert.equal(attempts,2);
});

test('host attach must roll back when U01 owner epoch, program or operation changes with recycled receipt ref',async()=>{
 for(const change of ['epoch','program','operation']){
  const state={epoch:epochA,programId:'JQ_CLI_V1',operation:'evaluate'};
  let reads=0,attaches=0;
  const detaches=[];
  const stableRef='host:stable:receipt';
  const connection={capabilities:async()=>{
    reads++;
    return {...source(state.epoch),program_id:state.programId,
      operations:[{name:state.operation,declared:true,verified:true}]};
  }};
  const config=()=>({
    programId:state.programId,
    bindings:[{operation:state.operation,id:'iwxa.cli.evaluate',version:'1.0.0'}],
    receipts:{[state.operation]:{observationRef:stableRef,
      observation:{...receipt(state.epoch).observation,
       program_id:state.programId,operation:state.operation}}}
  });
  const bridge=bindModuleToU01Host({
    descriptor:desc,host:{hostId:'iwxa.host.admin-center',version:'0.1.0'},
    port:connection,getBindings:config,
    hooks:{
      attach:async()=>{
        attaches++;
        if(change==='epoch')state.epoch=epochB;
        else if(change==='program')state.programId='YQ_CLI_V1';
        else state.operation='recompute';
        return {attached:true,bindingRef:'host:must-rollback'};
      },
      detach:async ref=>{detaches.push(ref);return {detached:true};}
    }
  });
  const outcome=await bridge.attach();
  assert.equal(outcome.attached,false,change+' must not attach under the old owner identity');
  assert.deepEqual(outcome.assessment.reasons,['U03_HOST_OBSERVATION_CHANGED']);
  assert.equal(outcome.rollback,'DETACHED');
  assert.equal(outcome.hostState,'DETACHED');
  assert.equal(outcome.hostReceipt.bindingRef,'host:must-rollback');
  assert.deepEqual(detaches,['host:must-rollback']);
  assert.equal(attaches,1);
  assert.equal(reads,2,'U03 must reuse the two existing U01 reads, not add another');
 }
});
