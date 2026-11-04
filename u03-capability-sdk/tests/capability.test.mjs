import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { MODULE_SCHEMA, STATUS_SCHEMA, assess, satisfies, validateDescriptor,
  validateStatus, createModuleAdapter } from '../src/capability.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const fixture = name => JSON.parse(readFileSync(resolve(root, 'fixtures', name), 'utf8'));
const moduleDescriptor = fixture('module.json');
const observations = fixture('observations.json');
const scenarios = fixture('scenarios.json');

for (const item of scenarios) {
  test(`U03 public JS consumer: ${item.name}`, () => {
    const descriptor = { ...structuredClone(moduleDescriptor), ...(item.descriptorOverrides ?? {}) };
    const result = assess(descriptor, item.host, observations[item.observations]);
    assert.deepEqual(result, { compatible: item.expected.length === 0, reasons: item.expected });
  });
}

test('shared JSON schemas are version-pinned and exclude authority', () => {
  const moduleSchema = JSON.parse(readFileSync(resolve(root, 'contracts/module-descriptor-v1.schema.json')));
  const statusSchema = JSON.parse(readFileSync(resolve(root, 'contracts/capability-status-v1.schema.json')));
  assert.equal(moduleSchema.properties.schema.const, MODULE_SCHEMA);
  assert.equal(statusSchema.properties.schema.const, STATUS_SCHEMA);
  assert.equal(moduleSchema.additionalProperties, false);
  assert.equal(statusSchema.additionalProperties, false);
  for (const schema of [moduleSchema, statusSchema]) {
    assert.ok(!('grants' in schema.properties));
    assert.ok(!('permissions' in schema.properties));
    assert.ok(!('install' in schema.properties));
    assert.ok(!('invoke' in schema.properties));
  }
  assert.equal(validateDescriptor(moduleDescriptor), true);
  assert.equal(validateStatus(observations.verified[0]), true);
});

test('version comparator is strict and fail-closed', () => {
  assert.equal(satisfies('1.2.0', '>=1.2.0 <2.0.0'), true);
  assert.equal(satisfies('2.0.0', '>=1.2.0 <2.0.0'), false);
  assert.equal(satisfies('1.2.0-beta', '>=1.0.0'), false);
  assert.equal(satisfies('1.2.0', '^1.0.0'), false);
  assert.equal(satisfies('1.2.0', '>=1.0.0 || >=2.0.0'), false);
  assert.equal(satisfies('1000000000000000000.0.0', '>999999999999999999.0.0'), true);
});

test('host lifecycle: rejection never calls attach, successful check does not mint grants', async () => {
  let calls = 0;
  const port = createModuleAdapter(moduleDescriptor, {
    attach: () => { calls += 1; return { attached: true, hostRef: 'host-issued:1' }; },
    detach: ref => ({ detached: true, ref }),
  });
  assert.deepEqual(port.describe(), moduleDescriptor);
  const refused = await port.attach({ hostId: 'iwxa.agent', version: '0.2.0' }, observations.verified);
  assert.equal(refused.attached, false);
  assert.equal(calls, 0);
  const allowed = await port.attach({ hostId: 'iwxa.agent', version: '0.3.0' }, observations.verified);
  assert.equal(calls, 1);
  assert.equal(allowed.attached, true);
  assert.deepEqual(allowed.hostReceipt, { attached: true, hostRef: 'host-issued:1' });
  assert.equal('grants' in allowed, false);
  assert.deepEqual(await port.detach('host-issued:1'), { detached: true, ref: 'host-issued:1' });
});

test('host lifecycle cannot claim attached without affirmative host receipt', async () => {
  const port = createModuleAdapter(moduleDescriptor, { attach: () => ({}), detach: () => null });
  const value = await port.attach({ hostId: 'iwxa.agent', version: '0.3.0' }, observations.verified);
  assert.equal(value.attached, false);
});

test('identifiers and versions do not accept trailing newline in JS', () => {
  assert.equal(validateDescriptor({ ...moduleDescriptor, id: 'iwxa.module\n' }), false);
  assert.equal(validateDescriptor({ ...moduleDescriptor, version: '0.1.0\n' }), false);
  assert.equal(validateDescriptor({ ...moduleDescriptor, requires: [{id:'iwxa.source.read',range:'>=1.0.0\n'}] }), false);
  assert.equal(validateStatus({ ...observations.verified[0], id: 'iwxa.source.read\n' }), false);
  assert.equal(satisfies('1.2.0\n', '>=1.0.0'), false);
  assert.equal(satisfies('1.2.0', '>=1.0.0\n'), false);
});
