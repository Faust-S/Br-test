// Cross-language parity: every declared capability row must match Python byte-for-byte
// after canonical JSON serialization. No external host/process or new registry.
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {projectProgramCard} from '../src/program-card.mjs';

const root=resolve(fileURLToPath(new URL('..',import.meta.url)));
const data=JSON.parse(readFileSync(resolve(root,'fixtures/program-card-scenarios.json'),'utf8'));
const expected=data.map(entry=>({case:entry.case,projection:projectProgramCard(entry.args)}));
const python=process.env.PYTHON??(process.platform==='win32'?'python':'python3');
const command=spawnSync(python,[resolve(root,'qualification/program_card_parity.py')],{
  cwd:root,encoding:'utf8',timeout:30000,
});
assert.equal(command.status,0,command.stderr||command.error?.message);
const actual=JSON.parse(command.stdout.trim());
assert.deepEqual(actual,expected);
console.log(JSON.stringify({schema:'IWXA_U03_PROGRAM_CARD_PARITY/1',status:'PASS',
  languages:['javascript','python'],cases:expected.length,
  rows:expected.reduce((n,x)=>n+x.projection.capabilities.length,0)}));
