import {createHash} from 'node:crypto';
import {sourceDescriptor,sourceObservation,requirePinnedSource} from './port.mjs';

const SIZE_LIMIT = 8 * 1024 * 1024;
const hash = data => 'sha256:' + createHash('sha256').update(data).digest('hex');

// Host supplies an authorized GET endpoint; no credential storage or invoke/apply.
export function createHttpSourcePort({endpoint,sourceId,fetcher=globalThis.fetch,
  authHeaders=async()=>({}),maxBytes=SIZE_LIMIT,now=()=>new Date().toISOString(),
  timeoutMs=10000}={}){
  let url;
  try {url=new URL(endpoint);} catch {throw Error('HTTP_SOURCE_ENDPOINT_INVALID');}
  const loopback = ['localhost','127.0.0.1','[::1]'].includes(url.hostname);
  if ((url.protocol!=='https:' && !(url.protocol==='http:'&&loopback)) ||
      url.username || url.password || url.hash ||
      !/^[a-z][a-z0-9._-]{1,79}$/.test(sourceId??'') ||
      typeof fetcher!=='function' || typeof authHeaders!=='function' ||
      !Number.isInteger(maxBytes) || maxBytes<1 || maxBytes>64*1024*1024 ||
      !Number.isInteger(timeoutMs) || timeoutMs<100 || timeoutMs>60000 ||
      typeof now!=='function') throw Error('HTTP_SOURCE_CONFIG_INVALID');
  const endpointFingerprint=createHash('sha256').update(url.toString()).digest('hex');
  const descriptor=sourceDescriptor({providerId:'http.api',sourceRef:'api://'+sourceId+'/'+endpointFingerprint,
    capabilities:[{id:'source.describe',available:true},{id:'source.refresh',available:true},
      {id:'source.read',available:true}]});
  const provenance={provider:'http',access:'host-provided-transport',
    endpointRef:sourceId,authority:'read-only HTTP GET observation'};
  const observe=(status,extra={})=>sourceObservation(descriptor,{status,observedAt:now(),provenance,...extra});

  async function capture(){
    let headers;
    try {headers=await authHeaders(sourceId);} catch {return {status:'AUTHORIZATION_REQUIRED'};}
    if(!headers || typeof headers!=='object' || Array.isArray(headers))
      return {status:'AUTHORIZATION_REQUIRED'};
    let response;
    try {response=await fetcher(url.toString(),{method:'GET',headers:{...headers},
      redirect:'error',signal:AbortSignal.timeout(timeoutMs)});}
    catch {return {status:'SOURCE_UNAVAILABLE'};}
    if(!response || typeof response.status!=='number')return {status:'SOURCE_INVALID'};
    // A host-supplied transport must not silently substitute another source.
    if(response.redirected===true ||
       (typeof response.url==='string' && response.url && response.url!==url.toString()))
      return {status:'SOURCE_UNAVAILABLE'};
    if(response.status===401)return {status:'AUTHORIZATION_REQUIRED'};
    if(response.status===403)return {status:'ACCESS_DENIED'};
    if(response.status===404)return {status:'SOURCE_MISSING'};
    if(response.status===429)return {status:'RATE_LIMITED'};
    if(!response.ok)return {status:'SOURCE_UNAVAILABLE'};
    // 202/203/206 or other 2xx responses do not prove a full source snapshot.
    if(response.status!==200)return {status:'SOURCE_INVALID'};
    const contentRange=response.headers?.get?.('content-range');
    if(contentRange!=null)return {status:'SOURCE_INVALID'};
    const sizeHeader=response.headers?.get?.('content-length');
    if(sizeHeader!=null && !/^(0|[1-9]\d*)$/.test(sizeHeader))
      return {status:'SOURCE_INVALID'};
    const contentCoding=response.headers?.get?.('content-encoding');
    const compressed=typeof contentCoding==='string' && contentCoding.trim()!=='' &&
      contentCoding.trim().toLowerCase()!=='identity';
    // fetch() exposes decoded bytes; Content-Length describes compressed wire bytes.
    if(!compressed && sizeHeader!=null && Number(sizeHeader)>maxBytes)
      return {status:'SOURCE_TOO_LARGE'};
    let buffer;
    if(response.body && typeof response.body.getReader==='function'){
      const reader=response.body.getReader();
      const chunks=[];let size=0;
      try {
        while(true){
          const {done,value}=await reader.read();
          if(done)break;
          if(!(value instanceof Uint8Array))return {status:'SOURCE_INVALID'};
          size+=value.byteLength;
          if(size>maxBytes)return {status:'SOURCE_TOO_LARGE'};
          chunks.push(value);
        }
        buffer=Buffer.concat(chunks.map(x=>Buffer.from(x)),size);
      } catch {return {status:'SOURCE_UNAVAILABLE'};}
      finally {try {await reader.cancel();}catch{}}
    } else if(typeof response.arrayBuffer==='function'){
      try {buffer=Buffer.from(await response.arrayBuffer());}catch{return {status:'SOURCE_UNAVAILABLE'};}
      if(buffer.length>maxBytes)return {status:'SOURCE_TOO_LARGE'};
    } else return {status:'SOURCE_INVALID'};
    if(!compressed && sizeHeader!=null && Number(sizeHeader)!==buffer.length)
      return {status:'SOURCE_INVALID'};
    return {status:'OK',revision:hash(buffer),buffer};
  }
  return Object.freeze({
    describe:()=>descriptor,
    refresh:async()=>{
      const r=await capture();
      return observe(r.status,r.status==='OK'?{revision:r.revision,contentRef:r.revision,bytes:r.buffer.length}:{});
    },
    read:async pinned=>{
      if(!requirePinnedSource(descriptor,pinned))return observe('REVISION_REQUIRED');
      const r=await capture();
      if(r.status!=='OK')return observe(r.status);
      if(r.revision!==pinned.revision)
        return observe('REVISION_MISMATCH',{revision:r.revision,contentRef:r.revision,bytes:r.buffer.length});
      return observe('OK',{revision:r.revision,contentRef:r.revision,bytes:r.buffer.length,
        content:{encoding:'base64',data:r.buffer.toString('base64')}});
    }
  });
}
