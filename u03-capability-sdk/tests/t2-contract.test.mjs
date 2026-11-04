import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { inspectT2Contract, isT2ManifestShape } from '../src/t2-manifest.mjs';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const read = name => JSON.parse(readFileSync(resolve(root, 'fixtures', name), 'utf8'));
const base = read('t2-manifest.json');
function apply(base, scenario) {
  const result = structuredClone(base);
  Object.assign(result, scenario.patch ?? {});
  Object.assign(result.compatibility ?? {}, scenario.compatibilityPatch ?? {});
  Object.assign(result.security ?? {}, scenario.securityPatch ?? {});
  if (scenario.omitContribution) delete result.contributions[scenario.omitContribution];
  return result;
}
for (const item of read('t2-scenarios.json')) {
  test(`T2 contract projection: ${item.name}`, () => {
    const manifest = apply(base, item);
    const result = inspectT2Contract(manifest,
      item.target ?? {workspaceContract:'0.1.0',runtimeContract:'1.3.0'});
    assert.deepEqual(result.reasons, item.expected);
    assert.equal(result.declaredCompatible, item.expected.length === 0);
    assert.equal(result.admission,'NOT_ASSESSED');
    assert.equal('grants' in result, false);
    assert.equal('activated' in result, false);
    assert.equal(result.module === null, item.expected.includes('U03_T2_MANIFEST_SHAPE_INVALID') ||
      item.expected.includes('U03_T2_TARGET_INVALID'));
    if (result.module) assert.equal(result.module.declaredContributionCounts.routes, 1);
  });
}
test('T2-only schema is never silently converted to U03 descriptor', () => {
  assert.equal(isT2ManifestShape(base), true);
  const projected = inspectT2Contract(base, {workspaceContract:'0.1.0',runtimeContract:'1.3.0'});
  assert.equal(projected.declaredCompatible,true);
  assert.equal(projected.module.id,base.module_id);
  assert.equal(projected.module.version,base.module_version);
  assert.equal('provides' in projected.module,false);
  assert.equal('hostCompatibility' in projected.module,false);
});
