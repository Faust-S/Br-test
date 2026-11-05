// Source-bound K02 <-> U03 consumer qualification, not a model registry/router.
// Uses the real pinned PUBLIC K02 API and U01 HTTP host port; the model inventory
// and generated text are synthetic. No real inference or quality is claimed.
import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createServer} from 'node:http';

import {assess, validateDescriptor} from '../src/capability.mjs';
import {projectU01Capabilities, observeU01CapabilitiesAsync} from '../src/u01-bridge.mjs';
import {createProductHostPort} from '../../universal-install/host-bindings/product-hosts.mjs';
import {HttpProgramConnection} from '../../universal-install/host-bindings/http-program.mjs';

const k02Root = resolve(process.env.U03_K02_PUBLIC_ROOT ?? 'k02-public');
const k02Source = resolve(k02Root, 'modules/k02-interoperability/index.mjs');
assert.equal(existsSync(k02Source), true, 'U03_K02_PINNED_PUBLIC_SOURCE_REQUIRED');
const k02 = await import(pathToFileURL(k02Source).href);
const {OllamaLocalModelProvider, MODEL_INPUT, MODEL_OUTPUT} = await import(
  pathToFileURL(resolve(k02Root, 'modules/k02-interoperability/adapters/ollama-local.mjs')).href);

const NAME = 'smollm2:135m-instruct-q2_K';
const PIN_A = 'a'.repeat(64);
const PIN_B = 'b'.repeat(64);
const PROGRAM_ID = 'MODEL_API_V1';
const OPERATION = 'generate';
const CAPABILITY_ID = 'iwxa.model.text.generate';

const u03Module = {
  schema: 'IWXA_MODULE_DESCRIPTOR/1.0.0',
  id: 'iwxa.u03.k02-model-consumer',
  version: '0.1.0',
  provides: [{id: CAPABILITY_ID, version: '1.0.0'}],
  requires: [{id: CAPABILITY_ID, range: '=1.0.0'}],
  hostCompatibility: [{hostId: 'iwxa.agent', range: '=0.3.0'}],
};
const u03Host = {hostId: 'iwxa.agent', version: '0.3.0'};
const mapping = () => ({
  programId: PROGRAM_ID,
  bindings: [{operation: OPERATION, id: CAPABILITY_ID, version: '1.0.0'}],
});
const pinned = {name: NAME, digest: PIN_A};
const solution = {
  solutionId: 'u03-k02-first-use',
  requirements: [{
    requirementId: 'generation', kind: 'model', capabilityId: 'text.generate',
    effectClass: 'MODEL_INFERENCE', inputSchema: MODEL_INPUT,
    outputSchema: MODEL_OUTPUT, required: true, inputModes: ['text'],
    outputModes: ['text'], modelPin: pinned,
  }],
};
const failedWith = code => error => error instanceof k02.InteropError && error.code === code;

function pinnedK02Provider(inventoryDigest = () => PIN_A) {
  return new OllamaLocalModelProvider({
    modelName: NAME,
    expectedDigest: PIN_A,
    fetchImpl: async url => {
      assert.equal(new URL(url).pathname, '/api/tags');
      return new Response(JSON.stringify({
        models: [{name: NAME, digest: inventoryDigest()}],
      }), {status: 200, headers: {'content-type': 'application/json'}});
    },
  });
}
async function planned(provider) {
  const descriptor = await k02.describeCapabilities(provider);
  const selection = k02.resolveRequirements(solution, [descriptor]);
  assert.equal(selection.status, 'SUPPORTED');
  const plan = k02.buildInvocationPlan({
    taskId: 'U03-K02-MODEL', solutionId: solution.solutionId,
  }, [], selection);
  assert.equal(plan.steps[0].invocationAuthority, 'HOST_ONLY');
  assert.equal(plan.steps[0].invocationState, 'NOT_INVOKED');
  assert.equal(plan.steps[0].proofOfInvoke, null);
  return {descriptor, selection, plan};
}

async function withU01Provider(run) {
  let mode = 'success';
  let requests = 0;
  let hostApproved = false;
  const server = createServer(async (req, res) => {
    if (req.method === 'GET' && req.url === '/health') {
      res.writeHead(200); res.end('ready'); return;
    }
    if (req.method !== 'POST' || req.url !== '/generate') {
      res.writeHead(404); res.end(); return;
    }
    requests += 1;
    for await (const _ of req) { /* Consume the test's real request body. */ }
    if (mode === 'unknown') {
      res.writeHead(503); res.end('ambiguous upstream outcome'); return;
    }
    res.writeHead(200, {'content-type': 'application/json'});
    res.end(JSON.stringify({
      text: 'synthetic model output', model: NAME, model_digest: PIN_A,
    }));
  });
  await new Promise((ok, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', ok);
  });
  try {
    const connection = new HttpProgramConnection({
      schema: 'IWXA_HTTP_PROGRAM/1', program_id: PROGRAM_ID,
      base_url: 'http://127.0.0.1:' + server.address().port,
      health_path: '/health',
      operations: {generate: {method: 'POST', path: '/generate'}},
    });
    const port = createProductHostPort('agent', ({operation, request}) => {
      if (operation === 'invoke') {
        if (!hostApproved) throw Error('HOST_APPROVAL_REQUIRED');
        return connection.invoke(request.operation, request.params);
      }
      return connection[operation]();
    });
    await run({
      port,
      getRequests: () => requests,
      approve: value => {hostApproved = value;},
      mode: value => {mode = value;},
    });
  } finally {
    await new Promise(ok => server.close(ok));
  }
}

test('U03 declaration and K02 model metadata remain different contracts', async () => {
  assert.equal(validateDescriptor(u03Module), true);
  assert.equal(u03Module.provides[0].version, '1.0.0');
  assert.equal('modelPin' in u03Module.provides[0], false);
  assert.equal('inputModes' in u03Module.provides[0], false);
  assert.equal('providerFeeUsd' in u03Module.provides[0], false);
  const {descriptor, selection, plan} = await planned(pinnedK02Provider());
  assert.equal(descriptor.availability, 'available'); // inventory probe, not execution
  assert.equal(descriptor.capabilities[0].modelPin.digest, PIN_A);
  assert.equal(descriptor.capabilities[0].supported, true);
  assert.equal(descriptor.capabilities[0].latencyMsP50, null);
  assert.equal(selection.estimates.latencyMs, null);
  assert.equal(selection.estimates.hostComputeCostUsd, null);
  assert.equal('qualityScore' in descriptor.capabilities[0], false);
  assert.equal(plan.steps[0].proofOfInvoke, null);
  assert.equal(plan.steps[0].invocationState, 'NOT_INVOKED');
});

test('first-use: K02 MATCHED_NOT_AUTHORIZED precedes U03 execution evidence', async () => {
  await withU01Provider(async ({port, getRequests, approve}) => {
    const provider = pinnedK02Provider();
    const {plan} = await planned(provider);
    const connected = await port.connect({});
    assert.equal(connected.status, 'CONNECTED');
    const current = await port.capabilities({});
    assert.equal(current.status, 'DECLARED');
    assert.deepEqual(current.operations, [
      {name: OPERATION, declared: true, verified: false},
    ]);
    const config = mapping();
    const before = (await observeU01CapabilitiesAsync(port, config))[0];
    assert.equal(before.available, false);
    assert.equal(before.reason, 'operation-unverified');
    assert.deepEqual(assess(u03Module, u03Host, [before]).reasons,
      ['U03_CAPABILITY_UNAVAILABLE:' + CAPABILITY_ID]);
    const readiness = await k02.verifyInvocationReadiness(
      plan, 'generation', provider, plan.planDigest);
    assert.equal(readiness.status, 'MATCHED_NOT_AUTHORIZED');
    assert.equal(getRequests(), 0);
    await assert.rejects(port.invoke({operation: OPERATION, params: {
      system: '', prompt: 'first model request', max_output_tokens: 3,
    }}), /HOST_APPROVAL_REQUIRED/);
    assert.equal(getRequests(), 0);
    approve(true); // Simulated owner approval; K02 and U03 never issue it.
    const observed = await port.invoke({operation: OPERATION, params: {
      system: '', prompt: 'first model request', max_output_tokens: 3,
    }});
    assert.equal(observed.status, 'EXECUTED');
    assert.equal(observed.executed, true);
    assert.equal(observed.connection_epoch, connected.connection_epoch);
    k02.validateInvocationOutput(plan.steps[0], JSON.parse(observed.output));
    assert.equal((await observeU01CapabilitiesAsync(port, config))[0].reason,
      'execution-receipt-missing');
    config.receipts = {generate: {
      observation: observed, observationRef: 'host:synthetic-model-invoke:1',
    }};
    const after = (await observeU01CapabilitiesAsync(port, config))[0];
    assert.equal(after.available, true);
    assert.equal(after.version, '1.0.0');
    assert.equal(assess(u03Module, u03Host, [after]).compatible, true);
    assert.equal(getRequests(), 1, 'Neither SDK may re-invoke the model');
    assert.equal('qualityScore' in after, false);
  });
});

test('stale U01 receipt does not become K02 evidence or authorize the new session', async () => {
  await withU01Provider(async ({port, approve, getRequests, mode}) => {
    const provider = pinnedK02Provider();
    const {plan} = await planned(provider);
    const config = mapping();
    approve(true);
    await port.connect({});
    const first = await port.invoke({operation: OPERATION, params: {prompt: 'first'}});
    assert.equal(first.status, 'EXECUTED');
    config.receipts = {generate: {
      observation: first, observationRef: 'host:previous-epoch:1',
    }};
    assert.equal((await observeU01CapabilitiesAsync(port, config))[0].available, true);
    await port.disconnect({});
    const recovered = await port.recover({});
    assert.notEqual(recovered.connection_epoch, first.connection_epoch);
    const second = await port.invoke({operation: OPERATION, params: {prompt: 'second'}});
    assert.equal(second.status, 'EXECUTED');
    const stale = (await observeU01CapabilitiesAsync(port, config))[0];
    assert.equal(stale.available, false);
    assert.equal(stale.reason, 'execution-receipt-missing');
    assert.equal(assess(u03Module, u03Host, [stale]).compatible, false);
    // K02 only matches the pinned provider and plan; it does not certify U01.
    assert.equal((await k02.verifyInvocationReadiness(
      plan, 'generation', provider, plan.planDigest)).status,
    'MATCHED_NOT_AUTHORIZED');

    mode('unknown');
    const unknown = await port.invoke({operation: OPERATION, params: {prompt: 'third'}});
    assert.equal(unknown.executionOutcome, 'UNKNOWN');
    assert.equal(unknown.status, 'HTTP_EXECUTION_FAILED');
    assert.equal((await observeU01CapabilitiesAsync(port, config))[0].reason,
      'operation-unverified');
    assert.equal(projectU01Capabilities(await port.capabilities({}), {
      ...config, receipts: {generate: {
        observation: unknown, observationRef: 'host:unknown:2',
      }},
    })[0].available, false);
    assert.equal(getRequests(), 3, 'No retry on UNKNOWN');
  });
});

test('version mismatch, changed model pin, and incomplete model metadata fail explicitly', async () => {
  const provider = pinnedK02Provider();
  const {plan, descriptor} = await planned(provider);
  const baseline = [{
    schema: 'IWXA_CAPABILITY_STATUS/1.0.0', id: CAPABILITY_ID,
    version: '1.0.0', available: true, observationRef: 'host:scoped:execution',
  }];
  assert.equal(assess(u03Module, u03Host, baseline).compatible, true);
  assert.deepEqual(assess(u03Module, {...u03Host, version: '0.2.0'}, baseline).reasons,
    ['U03_HOST_VERSION_INCOMPATIBLE']);
  assert.deepEqual(assess(u03Module, u03Host, [
    {...baseline[0], version: '0.9.0'},
  ]).reasons, ['U03_CAPABILITY_VERSION_INCOMPATIBLE:' + CAPABILITY_ID]);
  assert.deepEqual(assess(u03Module, u03Host, [
    {...baseline[0], available: false, reason: 'stale'},
  ]).reasons, ['U03_CAPABILITY_UNAVAILABLE:' + CAPABILITY_ID]);

  const changedPin = structuredClone(descriptor);
  changedPin.capabilities[0].modelPin.digest = PIN_B;
  await assert.rejects(k02.verifyInvocationReadiness(
    plan, 'generation',
    {describeCapabilities: async () => changedPin},
    plan.planDigest), failedWith('BINDING_CHANGED'));

  const noPin = structuredClone(descriptor);
  delete noPin.capabilities[0].modelPin;
  assert.equal(k02.resolveRequirements(solution, [noPin]).status, 'UNSUPPORTED');
  assert.equal(k02.resolveRequirements(solution, [noPin]).missing[0].reason,
    'PIN_MISMATCH');

  for (const missing of ['inputModes', 'outputModes']) {
    const incomplete = structuredClone(descriptor);
    delete incomplete.capabilities[0][missing];
    await assert.rejects(k02.describeCapabilities({
      describeCapabilities: async () => incomplete,
    }), failedWith('INVALID_DESCRIPTOR'));
  }
  const unknownCost = structuredClone(descriptor);
  unknownCost.capabilities[0].providerFeeUsd = null;
  unknownCost.capabilities[0].latencyMsP50 = null;
  const validated = await k02.describeCapabilities({
    describeCapabilities: async () => unknownCost,
  });
  const resolution = k02.resolveRequirements(solution, [validated]);
  assert.equal(resolution.estimates.providerFeeUsd, null);
  assert.equal(resolution.estimates.latencyMs, null);
  const fakeQuality = structuredClone(descriptor);
  fakeQuality.capabilities[0].qualityScore = 0.99;
  await assert.rejects(k02.describeCapabilities({
    describeCapabilities: async () => fakeQuality,
  }), failedWith('INVALID_DESCRIPTOR'));
});
