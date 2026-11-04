import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';

const worker=fileURLToPath(new URL('./atlas-center-restart-worker.mjs',import.meta.url));
const root=await mkdtemp(join(tmpdir(),'iwxa-u02-atlas-center-restart-'));
const steps=[];
try {
  for(const phase of ['create','resume','change','relink','reuse']) {
    const run=spawnSync(process.execPath,[worker,phase,root],{
      encoding:'utf8',timeout:15000,windowsHide:true
    });
    assert.equal(run.status,0,`${phase}: ${run.error?.message || run.stderr || run.stdout}`);
    const output=JSON.parse(run.stdout.trim());
    assert.equal(output.status,'PASS');
    assert.equal(output.phase,phase);
    steps.push(output);
  }
  assert.equal(steps[3].stale,'REVISION_MISMATCH');
  assert.equal(steps[4].historicalLinkUnchanged,true);
  console.log(JSON.stringify({schema:'IWXA_U02_ATLAS_CENTER_RESTART/1',status:'PASS',
    independentProcesses:steps.length,actualAtlasCore:true,actualCenterReader:true,
    pinnedReadAcrossRestart:true,staleRefused:true,historicalLinkPreserved:true,
    newLinkReusable:true,sourceWriteAuthority:false}));
} finally {
  await rm(root,{recursive:true,force:true});
}
