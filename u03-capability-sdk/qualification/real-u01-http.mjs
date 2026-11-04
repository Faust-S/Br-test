import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createServer } from 'node:http';
import { HttpProgramConnection } from '../../universal-install/host-bindings/http-program.mjs';
import { assess, createModuleAdapter } from '../src/capability.mjs';
import { observeU01Capabilities } from '../src/u01-bridge.mjs';

let requests = 0;
const server = createServer(async (req, res) => {
  if (req.headers.authorization !== 'Bearer host-approved') {
    res.writeHead(401);
    res.end('unauthorized');
    return;
  }
  if (req.url === '/health' && req.method === 'GET') {
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ ready: true }));
    return;
  }
  if (req.url === '/evaluate' && req.method === 'POST') {
    let body = '';
    for await (const chunk of req) {
      body += chunk;
      if (body.length > 4096) {
        res.writeHead(413);
        res.end('too large');
        return;
      }
    }
    let args;
    try {
      args = JSON.parse(body);
    } catch {
      res.writeHead(400);
      res.end('invalid JSON');
      return;
    }
    if (typeof args.a !== 'number' || !Number.isFinite(args.a) ||
        typeof args.b !== 'number' || !Number.isFinite(args.b)) {
      res.writeHead(400);
      res.end('invalid arguments');
      return;
    }
    requests += 1;
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ answer: args.a * args.b, requestNumber: requests }));
    return;
  }
  res.writeHead(404);
  res.end('not found');
});

await new Promise((resolve, reject) => {
  server.once('error', reject);
  server.listen(0, '127.0.0.1', resolve);
});

try {
  const spec = {
    schema: 'IWXA_HTTP_PROGRAM/1',
    program_id: 'U03_LOCAL_API_V1',
    base_url: `http://127.0.0.1:${server.address().port}`,
    health_path: '/health',
    operations: { evaluate: { method: 'POST', path: '/evaluate' } },
  };
  let token = 'host-approved';
  const connection = new HttpProgramConnection(spec, {
    authHeaders: async () => ({ Authorization: `Bearer ${token}` }),
  });
  const descriptor = {
    schema: 'IWXA_MODULE_DESCRIPTOR/1.0.0',
    id: 'iwxa.u03.http.consumer',
    version: '0.1.0',
    provides: [],
    requires: [{ id: 'iwxa.http.evaluate', range: '=1.0.0' }],
    hostCompatibility: [{ hostId: 'iwxa.u03.node.host', range: '>=0.1.0 <0.2.0' }],
  };
  const host = { hostId: 'iwxa.u03.node.host', version: '0.1.0' };
  const bindings = {
    programId: spec.program_id,
    bindings: [{ operation: 'evaluate', id: 'iwxa.http.evaluate', version: '1.0.0' }],
  };
  const observe = () => observeU01Capabilities(connection, bindings);
  const compatibility = () => assess(descriptor, host, observe());
  let attaches = 0;
  let detaches = 0;
  const adapter = createModuleAdapter(descriptor, {
    attach: async () => {
      attaches += 1;
      return { attached: true, bindingRef: `host-binding-${attaches}` };
    },
    detach: async (bindingRef) => {
      detaches += 1;
      return { detached: true, bindingRef };
    },
  });
  const tryAttach = async () => adapter.attach(host, observe());

  assert.equal((await tryAttach()).attached, false, 'disconnected bridge cannot attach');
  assert.equal(attaches, 0);
  const connected = await connection.connect();
  assert.equal(connected.status, 'CONNECTED');
  assert.equal(typeof connected.connection_epoch, 'string');
  assert.equal((await tryAttach()).attached, false, 'health check is not execution');
  assert.equal(connection.capabilities().operations[0].verified, false);

  const first = await connection.invoke('evaluate', { a: 6, b: 7 });
  assert.equal(first.status, 'EXECUTED');
  assert.equal(JSON.parse(first.output).answer, 42);
  assert.equal(first.connection_epoch, connected.connection_epoch);
  assert.equal((await tryAttach()).attached, false, 'verified flag without receipt is insufficient');

  const receipt = observation => ({
    observation,
    observationRef: 'host-observation:' +
      createHash('sha256').update(JSON.stringify({
        epoch: observation.connection_epoch,
        output: observation.output,
      })).digest('hex'),
  });
  bindings.receipts = { evaluate: receipt(first) };
  assert.equal(compatibility().compatible, true);
  assert.equal(assess(descriptor, { ...host, version: '0.2.0' }, observe()).compatible, false);
  const attached = await tryAttach();
  assert.equal(attached.attached, true);
  assert.equal(attached.hostReceipt.bindingRef, 'host-binding-1');
  assert.equal(attaches, 1);
  assert.equal('grants' in attached, false);
  assert.deepEqual(await adapter.detach(attached.hostReceipt.bindingRef), {
    detached: true,
    bindingRef: 'host-binding-1',
  });

  // Credentials are held by the consumer. After server rejects them, U01 loses
  // the session and U03 cannot reuse its old available status.
  token = 'expired';
  assert.equal((await connection.invoke('evaluate', { a: 5, b: 5 })).status,
    'AUTHORIZATION_REQUIRED');
  assert.equal(connection.capabilities().connected, false);
  assert.equal(connection.capabilities().connection_epoch, undefined);
  assert.equal((await tryAttach()).attached, false);
  assert.equal(attaches, 1);

  token = 'host-approved';
  const recovered = await connection.recover();
  assert.equal(recovered.status, 'CONNECTED');
  assert.notEqual(recovered.connection_epoch, first.connection_epoch);
  assert.equal((await tryAttach()).attached, false, 'recovery alone is not verified');
  const second = await connection.invoke('evaluate', { a: 7, b: 8 });
  assert.equal(second.status, 'EXECUTED');
  assert.equal(JSON.parse(second.output).answer, 56);
  assert.equal(connection.capabilities().operations[0].verified, true);
  assert.equal((await tryAttach()).attached, false,
    'previous receipt must fail after executing again in a new session');
  bindings.receipts = { evaluate: receipt(second) };
  assert.equal((await tryAttach()).attached, true);

  // Reverification opens another HTTP epoch. Even a previously successful
  // operation is unavailable without execution in the new epoch.
  const verified = await connection.verify();
  assert.equal(verified.status, 'CONNECTED');
  assert.notEqual(verified.connection_epoch, second.connection_epoch);
  assert.equal((await tryAttach()).attached, false);
  assert.equal(attaches, 2);
  connection.disconnect();
  assert.equal((await tryAttach()).attached, false);
  assert.equal(detaches, 1);
  assert.equal(requests, 2, 'only two real, successful calls must have been executed');
  console.log(JSON.stringify({
    schema: 'IWXA_U03_LIVE_HTTP_QUALIFICATION/1',
    status: 'PASS',
    transport: 'http',
    real_local_api: true,
    output_readback: [42, 56],
    calls: requests,
    host_owned_authentication: true,
    host_owned_attach_detach: true,
    authorization_revoked: true,
    stale_receipt_rejected: true,
    incompatible_host_rejected: true,
    private_product_modified: false,
  }));
} finally {
  await new Promise(resolve => server.close(resolve));
}
