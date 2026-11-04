// Thin U03 composition over an existing async U01 host port.
// Host remains responsible for authorization, lifecycle receipts and invocation.
import {assess,createModuleAdapter} from './capability.mjs';
import {observeU01CapabilitiesAsync} from './u01-bridge.mjs';

export function bindModuleToU01Host({descriptor,host,port,getBindings,hooks}={}){
  if(!port || typeof port.capabilities!=='function' || typeof getBindings!=='function')
    throw new TypeError('U03_HOST_BINDING_PORT_REQUIRED');
  const adapter=createModuleAdapter(descriptor,hooks);
  const owner={hostId:host?.hostId,version:host?.version};
  const observe=async()=>observeU01CapabilitiesAsync(port,await getBindings());
  return Object.freeze({
    describe:()=>adapter.describe(),
    assess:async()=>assess(adapter.describe(),owner,await observe()),
    attach:async()=>adapter.attach(owner,await observe()),
    detach:binding=>adapter.detach(binding),
  });
}
