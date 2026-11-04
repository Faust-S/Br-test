// Node consumer, with the already installed official jq binary passed by Python.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {basename,dirname,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {readFileSync} from 'node:fs';
import {CliConnection} from '../../universal-install/host-bindings/program-connection.mjs';
import {assess} from '../src/capability.mjs';
import {observeU01Capabilities} from '../src/u01-bridge.mjs';
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
console.log(JSON.stringify({schema:'IWXA_U03_REAL_U01_QUALIFICATION/1',consumer:'node',
 program:'jq',output:'42',status:'PASS'}));
