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
 assert.equal(calls,6);
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
