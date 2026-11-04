// All products use one protocol; real product-owned executor remains injected.
import {createHostAdapter} from "./host-port.mjs";
export function createProductHostPort(host,execute){
 if(!["agent","admin-center","atlas"].includes(host)||typeof execute!=="function")
  throw Error("PRODUCT_HOST_INVALID");
 const port=createHostAdapter(({schema,operation,request})=>
  execute({schema,host,operation,request}));
 return Object.freeze({host,contract:"IWXA_PROGRAM_CONNECTION/1.0.0",...port});
}
