import {spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {isAbsolute} from 'node:path';
import {sourceDescriptor,sourceObservation,requirePinnedSource} from './port.mjs';

const COMMIT=/^[a-f0-9]{40}(?:[a-f0-9]{24})?$/i;
const CAP=8*1024*1024;
const readGit=(root,revision,path,maxBytes)=>spawnSync('git',['-C',root,'show',revision+':'+path],{
  encoding:null,timeout:10000,maxBuffer:maxBytes+4096,shell:false,windowsHide:true,
  env:{...process.env,GIT_OPTIONAL_LOCKS:'0',GIT_TERMINAL_PROMPT:'0',GIT_NO_REPLACE_OBJECTS:'1'}
});
export function createLocalGitSourcePort({root,rootId,relativePath=null,observeLocalGit,
  readGitFile=readGit,maxBytes=CAP,now=()=>new Date().toISOString()}={}){
 if(typeof root!=='string'||!isAbsolute(root)||root.includes('\0')||
    typeof rootId!=='string'||!/^[a-z][a-z0-9_-]{0,63}$/.test(rootId)||
    typeof observeLocalGit!=='function'||typeof readGitFile!=='function'||
    typeof now!=='function'||!Number.isInteger(maxBytes)||maxBytes<1||maxBytes>64*1024*1024)
   throw Error('GIT_SOURCE_CONFIG_INVALID');
 if(relativePath!==null&&(typeof relativePath!=='string'||!relativePath||
   relativePath.length>1024||relativePath.includes('\\')||
   relativePath.split('/').some(s=>!s||s==='.'||s==='..'||/[\x00-\x1f\x7f:]/.test(s))))
   throw Error('GIT_SOURCE_PATH_INVALID');
 const descriptor=sourceDescriptor({providerId:'git.local',
   sourceRef:'git://'+rootId+(relativePath?'/'+relativePath.split('/').map(encodeURIComponent).join('/'):''),
   observationType:'repository',capabilities:[{id:'source.describe',available:true},
     {id:'source.refresh',available:true},{id:'source.read',available:relativePath!==null}]});
 const observe=(status,extra={})=>sourceObservation(descriptor,{status,observedAt:now(),
   provenance:{provider:'git',access:'host-selected-root',authority:'local-git-observation',
     rootId,path:relativePath},...extra});
 return Object.freeze({
   describe:()=>descriptor,
   refresh:async()=>{
     let view;
     try{view=await observeLocalGit(root)}catch{return observe('SOURCE_UNAVAILABLE')}
     if(!view||view.status!=='OK')return observe('SOURCE_UNAVAILABLE');
     if(!COMMIT.test(view.headSha??''))return observe('SOURCE_INVALID');
     if(view.dirty!==false)return observe('SOURCE_DIRTY',{revision:view.headSha});
     return observe('OK',{revision:view.headSha,detachedHead:view.detachedHead===true,
       branch:view.headRef??null,branchScope:'WORKTREE_ONLY'});
   },
   read:async pinned=>{
     if(relativePath===null)return observe('READ_UNSUPPORTED');
     if(!requirePinnedSource(descriptor,pinned)||!COMMIT.test(pinned.revision))
       return observe('REVISION_REQUIRED');
     let r;
     try{r=await readGitFile(root,pinned.revision,relativePath,maxBytes)}
     catch{return observe('SOURCE_UNAVAILABLE')}
     if(r?.error?.code==='ENOBUFS')return observe('SOURCE_TOO_LARGE');
     if(!r||r.error||r.status===null)return observe('SOURCE_UNAVAILABLE');
     if(r.status!==0)return observe('SOURCE_MISSING');
     if(!Buffer.isBuffer(r.stdout))return observe('SOURCE_INVALID');
     if(r.stdout.length>maxBytes)return observe('SOURCE_TOO_LARGE');
     const contentRef='sha256:'+createHash('sha256').update(r.stdout).digest('hex');
     return observe('OK',{revision:pinned.revision,contentRef,bytes:r.stdout.length,
       content:{encoding:'base64',data:r.stdout.toString('base64')}});
   }
 });
}
