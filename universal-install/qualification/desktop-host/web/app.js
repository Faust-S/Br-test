// The native command is called ONLY after a real click: no startup auto-invocation.
const button=document.getElementById("invoke");
const output=document.getElementById("result");
const schema="IWXA_PROGRAM_CONNECTION/1.0.0";
button.addEventListener("click", async () => {
 button.disabled=true;
 output.textContent="Checking the installed CLI...";
 try {
   const invoke=window.__TAURI__?.core?.invoke;
   if(typeof invoke!=="function")throw Error("TAURI_IPC_UNAVAILABLE");
   const request=(operation,payload={})=>invoke("iwxa_program_connection",{
     envelope:{schema,operation,request:payload}
   });
   const connected=await request("connect");
   if(connected.status!=="CONNECTED")throw Error("CONNECT_"+connected.status);
   const result=await request("invoke",{operation:"evaluate",params:{expression:"6 * 7"}});
   if(result.status!=="EXECUTED"||result.output.trim()!=="42")throw Error("EXECUTION_NOT_VERIFIED");
   const caps=await request("capabilities");
   if(caps.operations[0].verified!==true)throw Error("CAPABILITY_UNVERIFIED");
   const receipt=await invoke("qualification_evidence");
   if(receipt.status!=="PASS"||receipt.real_cli_output!=="42")throw Error("RECEIPT_INVALID");
   output.textContent="Verified result: 42";
 } catch(error) {
   output.textContent="Failure: "+String(error);
 } finally { button.disabled=false; }
});
