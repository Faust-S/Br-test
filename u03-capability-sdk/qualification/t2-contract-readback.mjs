// Inspect pinned public T2 source in place, without importing its runtime.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { inspectT2Contract, isExpectedT2Schema } from '../src/t2-manifest.mjs';
const root = process.argv[2];
if (!root) throw Error('U03_T2_PUBLIC_SOURCE_REQUIRED');
const base = resolve(root, 't2/current/final-successor/canonical-source/src');
const read = path => readFileSync(resolve(base, path), 'utf8');
const json = path => JSON.parse(read(path));
const schema = json('config/schemas/keys-2.0/workspace-module-manifest.schema.json');
assert.equal(isExpectedT2Schema(schema),true,'U03_T2_SCHEMA_DRIFT');
const versionCode = read('workspace-contract/version.ts');
const match = versionCode.match(/WORKSPACE_CONTRACT_VERSION\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"/);
assert.ok(match,'U03_T2_WORKSPACE_VERSION_UNAVAILABLE');
const workspace = match[1];
const runtime = json('kernel-client/contracts/runtime-1.3.0/contract-manifest.json').contract_version;
assert.equal(schema.properties.compatibility.properties.runtime_contract.const,runtime,
  'U03_T2_RUNTIME_SCHEMA_DRIFT');
const target = {workspaceContract:workspace, runtimeContract:runtime};
const modules = [];
for (const name of ['base','exchange','view-state','lifecycle']) {
  const manifest = json(`modules/${name}/manifest.json`);
  const result = inspectT2Contract(manifest,target);
  assert.deepEqual(result.reasons,[],`U03_T2_DECLARED_CONTRACT_FAILURE:${name}`);
  assert.equal(result.declaredCompatible,true);
  assert.equal(result.admission,'NOT_ASSESSED');
  assert.equal(result.module.id,manifest.module_id);
  assert.equal(result.module.version,manifest.module_version);
  assert.equal('provides' in result.module,false);
  assert.equal('grants' in result,false);
  assert.equal(inspectT2Contract(manifest,{...target,runtimeContract:'0.0.0'}).declaredCompatible,false);
  modules.push({id:result.module.id,version:result.module.version,
    declaredContributions:Object.values(result.module.declaredContributionCounts).reduce((a,b)=>a+b,0)});
}
console.log(JSON.stringify({schema:'IWXA_U03_T2_READBACK/1.0.0',status:'PASS',consumer:'node',
  owner:'T2',source:'public-pinned-checkout',workspaceContract:workspace,runtimeContract:runtime,
  modules,admission:'NOT_ASSESSED',hostGranted:false,hostModified:false}));
