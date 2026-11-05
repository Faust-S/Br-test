// A clean Node host using the installed @iwxa/capability-sdk and the
// existing U01 ProductHostPort. Each phase runs in a fresh OS process.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {existsSync, readFileSync, renameSync, writeFileSync} from 'node:fs';
import {basename} from 'node:path';
import {pathToFileURL} from 'node:url';
import {bindModuleToU01Host} from '@iwxa/capability-sdk/host-binding';

const [phase, statePath, executable, catalogPath, cliSourcePath, hostSourcePath] = process.argv.slice(2);
if (!['first', 'restart', 'reopen'].includes(phase) ||
    ![statePath, executable, catalogPath, cliSourcePath, hostSourcePath].every(Boolean))
  throw new Error('U03_RESTART_HOST_ARGUMENTS_INVALID');

const {CliConnection} = await import(pathToFileURL(cliSourcePath).href);
const {createProductHostPort} = await import(pathToFileURL(hostSourcePath).href);
const catalog = JSON.parse(readFileSync(catalogPath, 'utf8'));
const spec = {...catalog.programs.find(x => x.program_id === 'JQ_CLI_V1'),
  executable_name: basename(executable)};
assert.equal(spec.program_id, 'JQ_CLI_V1');
const connection = new CliConnection(spec, executable);
const port = createProductHostPort('atlas', ({operation, request}) =>
  operation === 'invoke' ? connection.invoke(request.operation, request.params) : connection[operation]());

const oldState = existsSync(statePath) ? JSON.parse(readFileSync(statePath, 'utf8')) : null;
if (phase === 'first') assert.equal(oldState, null, 'initial host state must be empty');
else {
  assert.equal(oldState.schema, 'IWXA_U03_RESTART_HOST_STATE/1');
  assert.deepEqual(oldState.results.map(x => x.value),
    phase === 'restart' ? [42] : [42, 56]);
}
const state = oldState ?? {schema: 'IWXA_U03_RESTART_HOST_STATE/1', results: [],
  lastEpoch: null, lastReceipt: null};
const bindings = {programId: spec.program_id,
  bindings: [{operation: 'evaluate', id: 'iwxa.cli.evaluate', version: '1.0.0'}],
  receipts: state.lastReceipt ? {evaluate: state.lastReceipt} : {}};
const descriptor = {schema: 'IWXA_MODULE_DESCRIPTOR/1.0.0',
  id: 'iwxa.restart.host.consumer', version: '0.1.0', provides: [],
  requires: [{id: 'iwxa.cli.evaluate', range: '=1.0.0'}],
  hostCompatibility: [{hostId: 'iwxa.host.atlas', range: '=0.1.0'}]};
const host = {hostId: 'iwxa.host.atlas', version: '0.1.0'};
let attached = 0, detached = 0;
const bridge = bindModuleToU01Host({
  descriptor, host, port, getBindings: () => bindings,
  hooks: {
    attach: async () => {
      attached += 1;
      return {attached: true, bindingRef: 'restart-host:' + attached};
    },
    detach: async ref => {
      assert.equal(ref, 'restart-host:' + attached);
      detached += 1;
      return {detached: true};
    },
  },
});
assert.equal(bridge.describe().id, descriptor.id);
assert.equal((await bridge.assess()).compatible, false);
assert.equal((await bridge.attach()).attached, false, 'no implicit connection allowed');
const connected = await port.connect({});
assert.equal(connected.status, 'CONNECTED');
assert.equal(typeof connected.connection_epoch, 'string');
if (oldState) {
  assert.notEqual(connected.connection_epoch, oldState.lastEpoch,
    'new process must open a new U01 session');
  assert.equal((await bridge.attach()).attached, false,
    'previous process receipt cannot authorize new attach');
}
if (phase !== 'reopen') {
  const previous = state.results.length ? state.results.at(-1).value : null;
  const expression = previous === null ? '6 * 7' : String(previous) + ' + 14';
  const observed = await port.invoke({operation: 'evaluate', params: {expression}});
  assert.equal(observed.status, 'EXECUTED');
  const value = Number(observed.output.trim());
  assert.equal(value, previous === null ? 42 : 56);
  assert.equal(observed.connection_epoch, connected.connection_epoch);
  assert.equal((await bridge.attach()).attached, false,
    'fresh operation alone does not create a host receipt');
  const ref = 'host-observation:' + createHash('sha256')
    .update(JSON.stringify({epoch: observed.connection_epoch, output: observed.output}))
    .digest('hex');
  bindings.receipts = {evaluate: {observation: observed, observationRef: ref}};
  assert.equal((await bridge.assess()).compatible, true);
  const granted = await bridge.attach();
  assert.equal(granted.attached, true);
  assert.equal((await bridge.detach(granted.hostReceipt.bindingRef)).detached, true);
  state.results.push({value, derivedFrom: previous, expression});
  state.lastReceipt = bindings.receipts.evaluate;
  state.lastEpoch = observed.connection_epoch;
  const staged = statePath + '.tmp';
  writeFileSync(staged, JSON.stringify(state), {encoding: 'utf8'});
  renameSync(staged, statePath);
  assert.deepEqual(JSON.parse(readFileSync(statePath, 'utf8')), state);
  assert.equal(attached, 1);
  assert.equal(detached, 1);
} else {
  assert.equal((await bridge.attach()).attached, false,
    'persisted results must not restore an obsolete U03 binding');
  assert.deepEqual(state.results.map(x => x.value), [42, 56]);
  assert.equal(state.results[1].derivedFrom, 42, 'second result must reuse first');
  assert.equal(attached, 0);
}
assert.equal((await port.disconnect({})).status, 'DISCONNECTED');
assert.equal((await bridge.assess()).compatible, false);
console.log(JSON.stringify({schema: 'IWXA_U03_RESTART_QUALIFICATION/1',
  phase, status: 'PASS', installedSdk: true, existingU01Port: true,
  hostOwnedPersistence: true, resultValues: state.results.map(x => x.value),
  oldReceiptRejected: oldState !== null, lifecycleCalls: attached,
  productHostModified: false}));
