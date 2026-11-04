// Plain JS contract router for Tauri/JS UI consumers.
// The native host owns process execution, authorization, persistence and UI.
export const CONTRACT="IWXA_PROGRAM_CONNECTION/1.0.0";
export function createHostAdapter(execute){
  if(typeof execute!=="function")throw Error("PROGRAM_HOST_EXECUTOR_REQUIRED");
  const act=async (operation,request={})=>{
    const response=await execute({schema:CONTRACT,operation,request});
    if(!response||response.schema!==CONTRACT||
       typeof response.status!=="string")
      throw Error("PROGRAM_HOST_INVALID_RESPONSE");
    return response;
  };
  return Object.freeze({
    inspect:request=>act("inspect",request),
    connect:request=>act("connect",request),
    verify:request=>act("verify",request),
    capabilities:request=>act("capabilities",request),
    invoke:request=>act("invoke",request),
    disconnect:request=>act("disconnect",request),
    recover:request=>act("recover",request)
  });
}
