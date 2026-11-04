import assert from 'node:assert/strict';
import {observePublicRepo} from '../../atlas-public/src/github-public.mjs';
import {createAtlasGitHubSourcePort} from '../atlas-github.mjs';
import {pinSource, sourceChange} from '../port.mjs';

const port = createAtlasGitHubSourcePort({repository: 'Faust-S/Atlas', branch: 'work/2026-11-02-atlas-desktop-0.2',
  relativePath: 'LICENSE', observePublicRepo});
// Public GitHub sometimes returns a transient network failure on Actions runners.
// Retry only transient observations; never turn an unavailable source into PASS.
const transient = new Set(['SOURCE_UNAVAILABLE', 'RATE_LIMITED']);
async function confirmed(operation) {
  let result;
  for (let attempt = 0; attempt < 4; attempt++) {
    result = await operation();
    if (!transient.has(result?.status)) return result;
    if (attempt < 3) await new Promise(resolve => setTimeout(resolve, 1000 * (attempt + 1)));
  }
  return result;
}
const first = await confirmed(() => port.refresh());
assert.equal(first.status, 'OK', JSON.stringify(first));
assert.match(first.revision, /^[a-f0-9]{40}$/);
const pinned = pinSource(port.describe(), first);
const actual = await confirmed(() => port.read(pinned));
assert.equal(actual.status, 'OK', JSON.stringify({...actual, content: null}));
assert.match(Buffer.from(actual.content.data, 'base64').toString('utf8'), /MIT License/);
const again = await confirmed(() => port.refresh());
assert.equal(again.status, 'OK');
assert.ok(['UNCHANGED', 'CHANGED'].includes(sourceChange(first, again).status));
assert.equal(actual.revision, pinned.revision);
assert.equal(actual.authority, 'OBSERVATION_ONLY');
console.log(JSON.stringify({schema: 'IWXA_U02_LIVE_GITHUB/1', status: 'PASS',
  repository: 'Faust-S/Atlas', branch: 'work/2026-11-02-atlas-desktop-0.2', revision: pinned.revision,
  bytes: actual.bytes, contentRef: actual.contentRef, publicAccess: true,
  secretRequired: false, readOnly: true}));
