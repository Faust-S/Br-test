// Thin U03 composition over an existing async U01 host port.
// The host owns authorization, connection, execution and detach receipts.
import {assess, createModuleAdapter} from './capability.mjs';
import {observeU01CapabilitiesAsync} from './u01-bridge.mjs';

export function bindModuleToU01Host({descriptor, host, port, getBindings, hooks}={}) {
  if (!port || typeof port.capabilities !== 'function' || typeof getBindings !== 'function')
    throw new TypeError('U03_HOST_BINDING_PORT_REQUIRED');
  const adapter = createModuleAdapter(descriptor, hooks);
  const owner = {hostId: host?.hostId, version: host?.version};
  const describe = () => adapter.describe();
  const observe = async () => observeU01CapabilitiesAsync(port, await getBindings());
  const refusedInFlight = () => ({attached: false,
    assessment: {compatible: false, reasons: ['U03_HOST_ATTACH_IN_PROGRESS']}, hostReceipt: null});
  let attaching = false; // Per-binding call guard, not a host registry or persistent state.
  return Object.freeze({
    describe,
    assess: async () => assess(describe(), owner, await observe()),
    attach: async () => {
      if (attaching) return refusedInFlight();
      attaching = true;
      try {
        const before = await observe();
        const result = await adapter.attach(owner, before);
        if (!result.attached) return result;
        const ref = result.hostReceipt?.bindingRef;
        if (typeof ref !== 'string' || ref.trim().length === 0) {
          // Host may have attached, but gave no usable reference for detach.
          return {attached: false,
            assessment: {compatible: false, reasons: ['U03_HOST_BINDING_REF_MISSING']},
            hostReceipt: result.hostReceipt, rollback: 'UNCONFIRMED', hostState: 'UNKNOWN'};
        }
        let after, assessment;
        try {
          after = await observe();
          assessment = assess(describe(), owner, after);
        } catch {
          assessment = {compatible: false, reasons: ['U03_HOST_OBSERVATION_LOST']};
        }
        if (assessment.compatible && JSON.stringify(before) === JSON.stringify(after))
          return result;
        if (assessment.compatible)
          assessment = {compatible: false, reasons: ['U03_HOST_OBSERVATION_CHANGED']};
        let rollback = 'UNCONFIRMED';
        try {
          const confirmation = await adapter.detach(ref);
          if (confirmation?.detached === true) rollback = 'DETACHED';
        } catch { /* Host owns recovery: do not claim detach. */ }
        return {attached: false, assessment, hostReceipt: result.hostReceipt,
          rollback, hostState: rollback === 'DETACHED' ? 'DETACHED' : 'UNKNOWN'};
      } finally {
        attaching = false;
      }
    },
    detach: binding => adapter.detach(binding),
  });
}
