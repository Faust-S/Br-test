// Thin U03 composition over an existing async U01 host port.
// The owner retains authorization, lifecycle receipts, invoke and final state.
import {assess,createModuleAdapter} from './capability.mjs';
import {observeU01CapabilitiesAsync} from './u01-bridge.mjs';

export function bindModuleToU01Host({descriptor,host,port,getBindings,hooks}={}){
  if(!port || typeof port.capabilities!=='function' || typeof getBindings!=='function')
    throw new TypeError('U03_HOST_BINDING_PORT_REQUIRED');
  const adapter=createModuleAdapter(descriptor,hooks);
  const owner={hostId:host?.hostId,version:host?.version};
  const observe=async()=>observeU01CapabilitiesAsync(port,await getBindings());
  const describe=()=>adapter.describe();
  return Object.freeze({
    describe,
    assess:async()=>assess(describe(),owner,await observe()),
    attach:async()=>{
      const before=await observe();
      const result=await adapter.attach(owner,before);
      if(!result.attached)return result;
      let after,assessment;
      try{
        after=await observe();
        assessment=assess(describe(),owner,after);
      }catch{
        assessment={compatible:false,reasons:['U03_HOST_OBSERVATION_LOST']};
      }
      if(assessment.compatible && JSON.stringify(before)===JSON.stringify(after))
        return result;
      if(assessment.compatible)
        assessment={compatible:false,reasons:['U03_HOST_OBSERVATION_CHANGED']};
      // A host-attached binding must be detached by the same host. U03 never
      // fabricates a bindingRef or claims rollback when the host cannot confirm it.
      const ref=result.hostReceipt?.bindingRef;
      let rollback='UNCONFIRMED';
      if(typeof ref==='string' && ref.length>0){
        try{
          const confirmation=await adapter.detach(ref);
          if(confirmation?.detached===true)rollback='DETACHED';
        }catch{/* Host owns recovery; report uncertainty explicitly. */}
      }
      return {attached:false,assessment,hostReceipt:result.hostReceipt,
        rollback,hostState:rollback==='DETACHED'?'DETACHED':'UNKNOWN'};
    },
    detach:binding=>adapter.detach(binding),
  });
}
