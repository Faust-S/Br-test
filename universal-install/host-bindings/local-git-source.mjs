// Read-only local Git observation. Does not initialize, fetch, checkout or modify.
import {spawnSync} from "node:child_process";
import {isAbsolute} from "node:path";

function command(root,args){
  const result=spawnSync("git",["-C",root,...args],{
    shell:false,encoding:"utf8",windowsHide:true,timeout:10000,maxBuffer:131072,
    env:{...process.env,GIT_OPTIONAL_LOCKS:"0",GIT_TERMINAL_PROMPT:"0"}
  });
  if(result.error||result.status!==0)return null;
  return result.stdout.trim();
}
export function observeLocalGit(root){
  if(typeof root!=="string"||!isAbsolute(root)||root.includes("\0"))
    throw Error("LOCAL_GIT_ROOT_INVALID");
  const sha=command(root,["rev-parse","HEAD"]);
  if(!sha||!/^[a-f0-9]{40,64}$/.test(sha))
    return {schema:"IWXA_LOCAL_GIT_SOURCE/1.0.0",scope:"repository",
            status:"SOURCE_UNAVAILABLE",complete:false,readiness:"not_assessed"};
  const head=command(root,["symbolic-ref","--quiet","--short","HEAD"]);
  const repo=command(root,["rev-parse","--show-toplevel"]);
  const dirty=command(root,["status","--porcelain","--untracked-files=no"]);
  const branches=head?[head]:[];
  const branchHeads={};
  if(head)branchHeads[head]=sha;
  return {schema:"IWXA_LOCAL_GIT_SOURCE/1.0.0",scope:"repository",
    status:"OK",complete:true,
    repository:repo,defaultBranch:null,branches,branchHeads,
    headSha:sha,headRef:head??null,detachedHead:head===null,
    dirty:dirty===null?null:dirty.length>0,
    observedAt:new Date().toISOString(),readiness:"not_assessed",
    provenance:{provider:"git",access:"local",endpoint:repo,
      authority:"Local Git read-only observation"}};
}
