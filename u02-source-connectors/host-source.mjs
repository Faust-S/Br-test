import {sourceDescriptor,sourceObservation,requirePinnedSource} from './port.mjs';

const DEFAULT_LIMIT=8*1024*1024;
const key=value=>typeof value==='string' && /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}$/.test(value);
const knownError=new Set(['AUTHORIZATION_REQUIRED','ACCESS_DENIED','CONNECTION_REQUIRED',
  'SOURCE_MISSING','SOURCE_UNAVAILABLE','SOURCE_TOO_LARGE']);
const sha256=async data=>{
  const subtle=globalThis.crypto?.subtle;
  if(!subtle)throw Error('SOURCE_CRYPTO_UNAVAILABLE');
  const hash=new Uint8Array(await subtle.digest('SHA-256',data));
  return 'sha256:'+Array.from(hash,x=>x.toString(16).padStart(2,'0')).join('');
};
const toBase64=data=>{
  if(typeof globalThis.btoa!=='function')throw Error('SOURCE_BASE64_UNAVAILABLE');
  const chunks=[];
  for(let n=0;n<data.length;n+=8192)
    chunks.push(String.fromCharCode(...data.subarray(n,n+8192)));
  return globalThis.btoa(chunks.join(''));
};

// The embedding host selects a source, authorizes it and reads it through
// its existing native port. U02 neither chooses files nor calls native APIs.
export function createHostSelectedSourcePort({hostId,scopeId,sourceId,bindingId,
  readSelected,maxBytes=DEFAULT_LIMIT,now=()=>new Date().toISOString()}={}){
  if(![hostId,scopeId,sourceId,bindingId].every(key)||
     typeof readSelected!=='function'||typeof now!=='function'||
     !Number.isInteger(maxBytes)||maxBytes<1||maxBytes>16*1024*1024)
    throw Error('HOST_SOURCE_CONFIG_INVALID');
  const descriptor=sourceDescriptor({providerId:'host.selected',
    sourceRef:`host://${hostId}/${scopeId}/${sourceId}/${bindingId}`});
  const provenance={provider:'host.selected',hostId,scopeId,sourceId,bindingId,
    access:'host-authorized-read',authority:'observation-only'};
  const observe=(status,extra={})=>sourceObservation(descriptor,{status,observedAt:now(),provenance,...extra});
  const readContext=Object.freeze({hostId,scopeId,sourceId,bindingId,maxBytes});
  async function capture(){
    let result;
    try{result=await readSelected(readContext);}catch{return {status:'SOURCE_UNAVAILABLE'};}
    if(!result||typeof result!=='object')return {status:'SOURCE_INVALID'};
    if(result.status!=='OK')return {status:knownError.has(result.status)?result.status:'SOURCE_INVALID'};
    if(!(result.bytes instanceof Uint8Array)||result.bytes.byteLength>maxBytes)
      return {status:result.bytes instanceof Uint8Array?'SOURCE_TOO_LARGE':'SOURCE_INVALID'};
    // Copy immediately: a host-owned mutable array must not change after digest.
    const bytes=Uint8Array.from(result.bytes);
    const revision=await sha256(bytes);
    return {status:'OK',revision,bytes};
  }
  return Object.freeze({
    describe:()=>descriptor,
    refresh:async()=>{
      const result=await capture();
      return observe(result.status,result.status==='OK'?{
        revision:result.revision,contentRef:result.revision,bytes:result.bytes.length}:{});
    },
    read:async pin=>{
      if(!requirePinnedSource(descriptor,pin))return observe('REVISION_REQUIRED');
      const result=await capture();
      if(result.status!=='OK')return observe(result.status);
      if(result.revision!==pin.revision)return observe('REVISION_MISMATCH',{
        revision:result.revision,contentRef:result.revision,bytes:result.bytes.length});
      return observe('OK',{revision:result.revision,contentRef:result.revision,
        bytes:result.bytes.length,content:{encoding:'base64',data:toBase64(result.bytes)}});
    }
  });
}
