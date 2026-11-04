// Node consumer, with the already installed official jq binary passed by Python.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {basename,dirname,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {readFileSync} from 'node:fs';
import {CliConnection} from '../../universal-install/host-bindings/program-connection.mjs';
import {assess} from '../src/capability.mjs';
import {observeU01Capabilities} from '../src/u01-bridge.mjs';
import {bindModuleToU01Host} from '../src/host-binding.mjs';
import {createProductHostPort} from '../../universal-install/host-bindings/product-hosts.mjs';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'../..');
const executable=process.argv[2];
if(!executable)throw Error('U03_JQ_EXECUTABLE_REQUIRED');
const catalog=JSON.parse(readFileSync(resolve(root,'universal-install/catalog/cli-connectors-1.json')));
const spec={...catalog.programs.find(x=>x.program_id==='JQ_CLI_V1'),executable_name:basename(executable)};
const c=new CliConnection(spec,executable);
const descriptor={schema:'IWXA_MODULE_DESCRIPTOR/1.0.0',id:'iwxa.u03.jq.node',version:'0.1.0',
 provides:[],requires:[{id:'iwxa.cli.evaluate',range:'=1.0.0'}],
 hostCompatibility:[{hostId:'iwxa.bridge.node',range:'=0.1.0'}]};
const host={hostId:'iwxa.bridge.node',version:'0.1.0'};
const config={programId:spec.program_id,bindings:[{operation:'evaluate',id:'iwxa.cli.evaluate',version:'1.0.0'}]};
const compatible=()=>assess(descriptor,host,observeU01Capabilities(c,config)).compatible;
assert.equal(compatible(),false);
assert.equal(c.connect().status,'CONNECTED');
assert.equal(compatible(),false);
const output=c.invoke('evaluate',{expression:'6 * 7'});
assert.equal(output.status,'EXECUTED');
assert.equal(output.output.trim(),'42');
assert.equal(compatible(),false);
config.receipts={evaluate:{observation:output,observationRef:'qualification:node:sha256:'+
 createHash('sha256').update(output.output).digest('hex')}};
assert.equal(compatible(),true);
assert.equal(assess(descriptor,{...host,version:'0.2.0'},observeU01Capabilities(c,config)).compatible,false);
c.disconnect();
assert.equal(compatible(),false);
assert.equal(c.recover().status,'CONNECTED');
assert.equal(compatible(),false);
const replay=c.invoke('evaluate',{expression:'6 * 7'});
assert.equal(replay.status,'EXECUTED');
assert.notEqual(replay.connection_epoch,output.connection_epoch);
assert.equal(compatible(),false,'old U01 receipt must not survive reconnect and fresh invoke');
config.receipts={evaluate:{observation:replay,observationRef:'qualification:node:epoch2:'+
 createHash('sha256').update(replay.output).digest('hex')}};
assert.equal(compatible(),true);
// Exercise the existing U01 asynchronous product host port with a REAL executable.
let attached=0,detached=0;
const productPort=createProductHostPort('admin-center',({operation,request})=>
  operation==='invoke'?c.invoke(request.operation,request.params):c[operation]());
const actualHost={hostId:'iwxa.host.admin-center',version:'0.1.0'};
const hostDescriptor={...descriptor,hostCompatibility:[{hostId:actualHost.hostId,range:'=0.1.0'}]};
const binding=bindModuleToU01Host({
  descriptor:hostDescriptor,host:actualHost,port:productPort,getBindings:()=>config,
  hooks:{
    attach:async()=>{attached++;return {attached:true,bindingRef:'existing-u01-host:1'};},
    detach:async ref=>{detached++;return {detached:true,bindingRef:ref};},
  },
});
assert.equal((await binding.assess()).compatible,true);
const live=await binding.attach();
assert.equal(live.attached,true);
assert.equal(live.hostReceipt.bindingRef,'existing-u01-host:1');
assert.equal((await binding.detach(live.hostReceipt.bindingRef)).detached,true);
assert.equal(attached,1);
assert.equal(detached,1);
assert.equal((await productPort.disconnect({})).status,'DISCONNECTED');
assert.equal((await binding.attach()).attached,false);
assert.equal(attached,1,'stale availability must not reconnect host');
console.log(JSON.stringify({schema:'IWXA_U03_REAL_U01_QUALIFICATION/1',consumer:'node',
 program:'jq',output:'42',status:'PASS'}));
