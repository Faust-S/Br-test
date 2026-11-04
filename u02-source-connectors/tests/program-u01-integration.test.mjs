import test from 'node:test';
import assert from 'node:assert/strict';
import {basename} from 'node:path';
import {CliConnection,SCHEMA} from '../../universal-install/host-bindings/program-connection.mjs';
import {createProductHostPort} from '../../universal-install/host-bindings/product-hosts.mjs';
import {createProgramCapabilityView} from '../program-view.mjs';

// Real installed Node CLI through U01, two independent host-port doubles.
// Does not claim installed Agent/Atlas GUI integration.
test('two U01 consumers observe a real CLI without implicit invoke',async()=>{
 const spec={schema:'IWXA_CLI_PROGRAM/1',program_id:'NODE_CLI_U02',
  executable_name:basename(process.execPath),version_args:['--version'],
  version_contains:process.version,operations:{evaluate:['-p','{expression}']}};
 for(const host of ['agent','atlas']){
  const conn=new CliConnection(spec,process.execPath);
  let invokes=0,connects=0;
  const executor=async({schema,host:requested,operation,request})=>{
   assert.equal(schema,SCHEMA);
   assert.equal(requested,host);
   if(operation==='invoke'){invokes++;return conn.invoke(request.operation,request.params);}
   if(operation==='connect')connects++;
   return conn[operation]();
  };
  const consumer=createProductHostPort(host,executor);
  const source=createProgramCapabilityView({programId:spec.program_id,connection:consumer});
  const before=await source.refresh();
  assert.equal(before.status,'OK');
  assert.equal(before.connected,false);
  assert.equal(before.programCapabilities[0].verified,false);
  assert.equal(invokes,0);
  assert.equal(connects,0);
  assert.equal((await source.read()).status,'READ_UNSUPPORTED');
  assert.equal((await consumer.connect({})).status,'CONNECTED');
  const executed=await consumer.invoke({operation:'evaluate',params:{expression:'6*7'}});
  assert.equal(executed.status,'EXECUTED');
  assert.equal(executed.output.trim(),'42');
  const actual=await source.refresh();
  assert.equal(actual.connected,true);
  assert.equal(actual.programCapabilities[0].verified,true);
  assert.equal(invokes,1);
  assert.equal((await consumer.disconnect({})).status,'DISCONNECTED');
  assert.equal((await source.refresh()).programCapabilities[0].verified,false);
  assert.equal((await consumer.recover({})).status,'CONNECTED');
  assert.equal((await source.refresh()).programCapabilities[0].verified,false);
  assert.equal(invokes,1);
 }
});
