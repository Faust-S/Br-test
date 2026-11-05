import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {test} from 'node:test';
import {HttpProgramConnection} from '../../universal-install/host-bindings/http-program.mjs';
import {createProductHostPort} from '../../universal-install/host-bindings/product-hosts.mjs';
import {projectU01Capabilities, observeU01CapabilitiesAsync} from '../src/u01-bridge.mjs';
import {bindModuleToU01Host} from '../src/host-binding.mjs';

const programId = 'U03_FIRST_USE_V1';
const operation = 'evaluate';
const capabilityId = 'iwxa.http.evaluate';
const descriptor = {
  schema: 'IWXA_MODULE_DESCRIPTOR/1.0.0',
  id: 'iwxa.u03.first-use.consumer',
  version: '0.1.0',
  provides: [],
  requires: [{id: capabilityId, range: '=1.0.0'}],
  hostCompatibility: [{hostId: 'iwxa.agent', range: '=0.3.0'}],
};
const host = {hostId: 'iwxa.agent', version: '0.3.0'};
const bindings = () => ({
  programId,
  bindings: [{operation, id: capabilityId, version: '1.0.0'}],
});

async function withExistingU01Host(run) {
  let mode = 'success';
  let calls = 0;
  const server = createServer(async (request, response) => {
    if (request.url === '/health' && request.method === 'GET') {
      response.writeHead(200);
      response.end('healthy');
      return;
    }
    if (request.url !== '/evaluate' || request.method !== 'POST') {
      response.writeHead(404);
      response.end();
      return;
    }
    calls += 1;
    if (mode === 'unknown') {
      response.writeHead(503);
      response.end('upstream result unknown');
      return;
    }
    let body = '';
    for await (const chunk of request) body += chunk;
    const params = JSON.parse(body);
    response.writeHead(200, {'content-type': 'application/json'});
    response.end(JSON.stringify({value: params.a * params.b}));
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  try {
    const connection = new HttpProgramConnection({
      schema: 'IWXA_HTTP_PROGRAM/1',
      program_id: programId,
      base_url: 'http://127.0.0.1:' + server.address().port,
      health_path: '/health',
      operations: {evaluate: {method: 'POST', path: '/evaluate'}},
    });
    const port = createProductHostPort('agent', async ({operation: method, request}) =>
      method === 'invoke'
        ? connection.invoke(request.operation, request.params)
        : connection[method]());
    await run({port, setMode: value => {mode = value;}, calls: () => calls});
  } finally {
    await new Promise(resolve => server.close(resolve));
  }
}

test('first use: U01 DECLARED permits owner-authorized invoke without a U03 receipt', async () => {
  await withExistingU01Host(async ({port, calls}) => {
    const discovered = await port.inspect({});
    assert.equal(discovered.status, 'CONFIGURED'); // HTTP != installed CLI binary
    assert.equal(discovered.installed, false);
    const connection = await port.connect({});
    assert.equal(connection.status, 'CONNECTED');
    assert.equal(connection.connected, true);
    const declared = await port.capabilities({});
    assert.equal(declared.status, 'DECLARED');
    assert.equal(declared.connected, true);
    assert.deepEqual(declared.operations, [
      {name: operation, declared: true, verified: false},
    ]);

    // U03 confirms execution, not the host's authority to offer a first call.
    const config = bindings();
    const firstProjection = (await observeU01CapabilitiesAsync(port, config))[0];
    assert.equal(firstProjection.available, false);
    assert.equal(firstProjection.reason, 'operation-unverified');
    let hostAttaches = 0;
    let hostDetaches = 0;
    const moduleBinding = bindModuleToU01Host({
      descriptor, host, port, getBindings: () => config,
      hooks: {
        attach: async () => {
          hostAttaches += 1;
          return {attached: true, bindingRef: 'host:first-use:1'};
        },
        detach: async ref => {
          assert.equal(ref, 'host:first-use:1');
          hostDetaches += 1;
          return {detached: true};
        },
      },
    });
    assert.equal((await moduleBinding.attach()).attached, false);
    assert.equal(hostAttaches, 0);
    assert.equal(calls(), 0);

    // The existing U01 port performs the first call after host approval.
    const result = await port.invoke({operation, params: {a: 6, b: 7}});
    assert.equal(result.status, 'EXECUTED');
    assert.equal(result.executed, true);
    assert.equal(result.connection_epoch, connection.connection_epoch);
    assert.equal(JSON.parse(result.output).value, 42);
    assert.equal(calls(), 1);

    const missingReceipt = (await observeU01CapabilitiesAsync(port, config))[0];
    assert.equal(missingReceipt.available, false);
    assert.equal(missingReceipt.reason, 'execution-receipt-missing');
    config.receipts = {
      evaluate: {observation: result, observationRef: 'owner:task:result:42'},
    };
    const confirmed = (await observeU01CapabilitiesAsync(port, config))[0];
    assert.equal(confirmed.available, true);
    assert.equal((await moduleBinding.assess()).compatible, true);
    const attached = await moduleBinding.attach();
    assert.equal(attached.attached, true);
    assert.equal((await moduleBinding.detach(attached.hostReceipt.bindingRef)).detached, true);
    assert.equal(hostAttaches, 1);
    assert.equal(hostDetaches, 1);
    assert.equal(calls(), 1, 'U03 must not execute the operation again');
  });
});

test('stale epoch and UNKNOWN outcome never confirm U03 or cause automatic replay', async () => {
  await withExistingU01Host(async ({port, setMode, calls}) => {
    const config = bindings();
    const firstConnection = await port.connect({});
    const first = await port.invoke({operation, params: {a: 2, b: 3}});
    assert.equal(first.status, 'EXECUTED');
    config.receipts = {evaluate: {
      observation: first, observationRef: 'owner:old:result:6',
    }};
    assert.equal((await observeU01CapabilitiesAsync(port, config))[0].available, true);

    await port.disconnect({});
    assert.equal((await observeU01CapabilitiesAsync(port, config))[0].available, false);
    const secondConnection = await port.recover({});
    assert.equal(secondConnection.status, 'CONNECTED');
    assert.notEqual(secondConnection.connection_epoch, firstConnection.connection_epoch);

    const second = await port.invoke({operation, params: {a: 3, b: 4}});
    assert.equal(second.status, 'EXECUTED');
    assert.equal(second.executed, true);
    assert.equal((await observeU01CapabilitiesAsync(port, config))[0].reason,
      'execution-receipt-missing', 'verified=true cannot revive a previous epoch receipt');

    setMode('unknown');
    const unknown = await port.invoke({operation, params: {a: 3, b: 5}});
    assert.equal(unknown.status, 'HTTP_EXECUTION_FAILED');
    assert.equal(unknown.executionOutcome, 'UNKNOWN');
    assert.notEqual(unknown.executed, true);
    const current = await port.capabilities({});
    assert.equal(current.operations[0].verified, false);
    assert.equal(projectU01Capabilities(current, config)[0].reason, 'operation-unverified');
    const unknownAsReceipt = {...config, receipts: {evaluate: {
      observation: unknown, observationRef: 'owner:unknown:outcome',
    }}};
    assert.equal(projectU01Capabilities(current, unknownAsReceipt)[0].available, false);
    assert.equal(projectU01Capabilities({
      schema: current.schema, program_id: programId, transport: 'http',
      connected: false, status: 'UNKNOWN', operations: current.operations,
    }, config)[0].reason, 'source-unavailable');

    let hostAttaches = 0;
    const binding = bindModuleToU01Host({
      descriptor, host, port, getBindings: () => unknownAsReceipt,
      hooks: {
        attach: async () => {hostAttaches += 1; return {attached: true, bindingRef: 'never'};},
        detach: async () => ({detached: true}),
      },
    });
    assert.equal((await binding.attach()).attached, false);
    assert.equal(hostAttaches, 0);
    assert.equal(calls(), 3, 'UNKNOWN must not trigger a fourth automatic invoke');
  });
});
