import assert from 'node:assert/strict';
import {observePublicRepo} from '../../atlas-public/src/github-public.mjs';
import {createAtlasGitHubSourcePort} from '../atlas-github.mjs';
import {pinSource, sourceChange} from '../port.mjs';

const port = createAtlasGitHubSourcePort({repository: 'Faust-S/Atlas', branch: 'work/2026-11-02-atlas-desktop-0.2',
  relativePath: 'LICENSE', observePublicRepo});
const first = await port.refresh();
assert.equal(first.status, 'OK', JSON.stringify(first));
assert.match(first.revision, /^[a-f0-9]{40}$/);
const pinned = pinSource(port.describe(), first);
const actual = await port.read(pinned);
assert.equal(actual.status, 'OK', JSON.stringify({...actual, content: null}));
assert.match(Buffer.from(actual.content.data, 'base64').toString('utf8'), /MIT License/);
const again = await port.refresh();
assert.equal(again.status, 'OK');
assert.ok(['UNCHANGED', 'CHANGED'].includes(sourceChange(first, again).status));
assert.equal(actual.revision, pinned.revision);
assert.equal(actual.authority, 'OBSERVATION_ONLY');
console.log(JSON.stringify({schema: 'IWXA_U02_LIVE_GITHUB/1', status: 'PASS',
  repository: 'Faust-S/Atlas', branch: 'work/2026-11-02-atlas-desktop-0.2', revision: pinned.revision,
  bytes: actual.bytes, contentRef: actual.contentRef, publicAccess: true,
  secretRequired: false, readOnly: true}));
