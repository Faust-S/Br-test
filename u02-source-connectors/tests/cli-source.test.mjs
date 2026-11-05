import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, rm, writeFile} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import {basename, join} from 'node:path';
import {tmpdir} from 'node:os';
import {createHash} from 'node:crypto';
import {CliConnection, SCHEMA} from '../../universal-install/host-bindings/program-connection.mjs';
import {createProductHostPort} from '../../universal-install/host-bindings/product-hosts.mjs';
import {createCliOutputSourcePort} from '../cli-source.mjs';
import {captureSource, readPinnedSource} from '../source-read.mjs';
import {pinSource, sourceChange} from '../port.mjs';

const digest = text => 'sha256:' + createHash('sha256').update(text).digest('hex');
const success = (output, operation = 'read_source') => ({
  schema: SCHEMA, program_id: 'NODE_CLI_U02', status: 'EXECUTED',
  executed: true, operation, output
});
const options = {programId:'NODE_CLI_U02', sourceId:'source.document',
  operationId:'read_source',contextId:'local_v1'};

test('CLI output snapshot is pinned and detects changes without applying them', async () => {
  let output='first',calls=0;
  const port = createCliOutputSourcePort({...options, readOutput: async () => {
    calls++;return success(output);
  }});
  assert.equal(calls,0);
  assert.equal((await port.read(port.describe())).status,'REVISION_REQUIRED');
  assert.equal(calls,0);
  const first=await captureSource(port);
  assert.equal(first.status,'OK');
  assert.equal(first.pin.revision,digest('first'));
  assert.equal(Buffer.from(first.read.content.data,'base64').toString(),'first');
  assert.equal(calls,2);
  output='second';
  const stale=await readPinnedSource(port,JSON.parse(JSON.stringify(first.pin)));
  assert.equal(stale.status,'REVISION_MISMATCH');
  assert.equal(stale.content,null);
  const changed=await captureSource(port);
  assert.equal(changed.status,'OK');
  assert.equal(changed.pin.revision,digest('second'));
  assert.equal(sourceChange(first.observation,changed.observation).status,'CHANGED');
  assert.equal(changed.read.authority,'OBSERVATION_ONLY');
});

test('CLI source identity binds host context and never runs with an invalid pin', async () => {
  let calls=0;
  const readOutput=async()=>{calls++;return success('same');};
  const original=createCliOutputSourcePort({...options,readOutput});
  const alternative=createCliOutputSourcePort({...options,contextId:'another_v1',readOutput});
  assert.notEqual(original.describe().sourceRef,alternative.describe().sourceRef);
  const pin=pinSource(original.describe(),await original.refresh());
  const before=calls;
  assert.equal((await alternative.read(pin)).status,'REVISION_REQUIRED');
  assert.equal(calls,before);
  await assert.rejects(()=>readPinnedSource(alternative,pin),/SOURCE_PIN_INVALID/);
  assert.equal(calls,before);
  assert.throws(()=>createCliOutputSourcePort({...options,contextId:'secret/token',readOutput}),
    /CLI_SOURCE_CONFIG_INVALID/);
  assert.throws(()=>createCliOutputSourcePort({...options,readOutput:null}),
    /CLI_SOURCE_CONFIG_INVALID/);
});

test('CLI read statuses and invalid responses cannot become source content', async () => {
  const status=[['NOT_CONNECTED','CONNECTION_REQUIRED'],
    ['CONNECTION_LOST','CONNECTION_REQUIRED'],
    ['AUTHORIZATION_REQUIRED','AUTHORIZATION_REQUIRED'],
    ['ACCESS_DENIED','ACCESS_DENIED'],
    ['OPERATION_UNSUPPORTED','READ_UNSUPPORTED'],
    ['CLI_TIMED_OUT','SOURCE_UNAVAILABLE'],
    ['EXECUTION_FAILED','SOURCE_UNAVAILABLE']];
  for(const [input,expected] of status) {
    const port=createCliOutputSourcePort({...options,readOutput:async()=>({
      schema:SCHEMA,program_id:options.programId,status:input,output:'private data'
    })});
    const view=await captureSource(port);
    assert.equal(view.status,expected);
    assert.equal(view.pin,null);
    assert.equal(view.read,null);
    assert.ok(!JSON.stringify(view).includes('private data'));
  }
  for(const invalid of [
    {...success('bad'),program_id:'OTHER'},
    {...success('bad'),operation:'write_source'},
    {...success('bad'),executed:false},
    {...success('bad'),output:null}
  ]) {
    const port=createCliOutputSourcePort({...options,readOutput:async()=>invalid});
    assert.equal((await port.refresh()).status,'SOURCE_INVALID');
  }
  const large=createCliOutputSourcePort({...options,maxBytes:3,
    readOutput:async()=>success('Привет')});
  assert.equal((await large.refresh()).status,'SOURCE_TOO_LARGE');
  const broken=createCliOutputSourcePort({...options,readOutput:async()=>{throw Error('secret')}});
  assert.equal((await broken.refresh()).status,'SOURCE_UNAVAILABLE');
});

test('real U01 Node CLI serves a source through three host ports without implicit connect',async()=>{
  const root=await mkdtemp(join(tmpdir(),'iwxa-u02-cli-source-'));
  const file=join(root,'source.txt');
  try {
    for (const hostName of ['agent','admin-center','atlas']) {
      await writeFile(file,'first');
      const spec={schema:'IWXA_CLI_PROGRAM/1',program_id:'NODE_CLI_U02',
        executable_name:basename(process.execPath),
        version_args:['--version'],version_contains:process.version,
        operations:{read_source:['-p','{expression}']}};
      const connection=new CliConnection(spec,process.execPath);
      let invoked=0,connected=0,authorized=true;
      const host=createProductHostPort(hostName,async ({schema,host,operation,request})=>{
        assert.equal(schema,SCHEMA);
        assert.equal(host,hostName);
        if(operation==='invoke') {
          invoked++;
          return connection.invoke(request.operation,request.params);
        }
        if(operation==='connect')connected++;
        return connection[operation](request);
      });
      const expression=`require('node:fs').readFileSync(${JSON.stringify(file)},'utf8')`;
      const source=createCliOutputSourcePort({...options,contextId:hostName.replace('-','_')+'_v1',
        readOutput:async()=> authorized
          ? host.invoke({operation:'read_source',params:{expression}})
          : {schema:SCHEMA,program_id:spec.program_id,status:'AUTHORIZATION_REQUIRED'}});
      assert.equal(invoked,0);
      assert.equal(connected,0);
      assert.equal((await source.refresh()).status,'CONNECTION_REQUIRED');
      assert.equal(connected,0);
      assert.equal(invoked,1);
      assert.equal((await host.connect({})).status,'CONNECTED');
      assert.equal(connected,1);
      const first=await captureSource(source);
      assert.equal(first.status,'OK');
      assert.equal(Buffer.from(first.read.content.data,'base64').toString().trim(),'first');
      assert.equal(first.read.authority,'OBSERVATION_ONLY');
      assert.equal(first.pin.revision,first.observation.revision);
      await writeFile(file,'second');
      const stale=await readPinnedSource(source,JSON.parse(JSON.stringify(first.pin)));
      assert.equal(stale.status,'REVISION_MISMATCH');
      assert.equal(stale.content,null);
      authorized=false;
      const denied=await captureSource(source);
      assert.equal(denied.status,'AUTHORIZATION_REQUIRED');
      assert.equal(denied.pin,null);
      const before=invoked;
      assert.equal(invoked,before);
      authorized=true;
      assert.equal((await host.disconnect({})).status,'DISCONNECTED');
      assert.equal((await source.refresh()).status,'CONNECTION_REQUIRED');
      assert.equal(connected,1);
      assert.equal((await host.recover({})).status,'CONNECTED');
      const next=await captureSource(source);
      assert.equal(next.status,'OK');
      assert.equal(Buffer.from(next.read.content.data,'base64').toString().trim(),'second');
    }
  } finally {
    await rm(root,{recursive:true,force:true});
  }
});
