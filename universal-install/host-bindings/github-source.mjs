// Atlas-compatible source observation adapter. Host owns credentials and storage.
// This module never upgrades a public observation into private authority.
const SCHEMA="IWXA_GITHUB_SOURCE/1.0.0";
const validRepo=/^[a-z\d](?:[a-z\d-]{0,37}[a-z\d])?\/[a-z\d_.-]+$/i;
export function repositoryName(value) {
  if(typeof value!=="string"||!validRepo.test(value)||value.includes(".."))
    throw Error("SOURCE_REPOSITORY_INVALID");
  return value;
}
function observation(repo,access,status,detail={}) {
  return {schema:SCHEMA,scope:"repository",repository:repo,access,status,
    complete:false,readiness:"not_assessed",...detail};
}
export async function observeAuthorizedGitHubRepo(repository,{
  fetcher, credentialProvider, maxPages=20, signal
}={}) {
  const repo=repositoryName(repository);
  if(typeof fetcher!=="function"||typeof credentialProvider!=="function")
    return observation(repo,"authorized","AUTHORIZATION_REQUIRED");
  if(!Number.isInteger(maxPages)||maxPages<1||maxPages>50)
    throw Error("SOURCE_PAGINATION_INVALID");
  // Secret resolution is an explicit host callback, never a process global.
  let token;
  try{token=await credentialProvider("github",repo);}
  catch{return observation(repo,"authorized","AUTHORIZATION_REQUIRED");}
  if(typeof token!=="string"||!token)
    return observation(repo,"authorized","AUTHORIZATION_REQUIRED");
  const url="https://api.github.com/repos/"+repo;
  const headers={Accept:"application/vnd.github+json",Authorization:"Bearer "+token};
  const validSha=value=>typeof value==="string"&&/^[a-f0-9]{40,64}$/i.test(value);
  const safeBranch=value=>typeof value==="string"&&value.length>0&&
    value.length<=255&&!/[\u0000-\u001f\u007f]/.test(value);
  const request=async path=>{
    try {return await fetcher(path,{headers,signal});}
    catch{return null;}
  };
  const response=await request(url);
  if(!response)return observation(repo,"authorized","SOURCE_UNAVAILABLE");
  if(response.status===401||response.status===403)
    return observation(repo,"authorized","ACCESS_DENIED");
  if(response.status===404)
    // A hidden private repository is indistinguishable from nonexistence.
    return observation(repo,"authorized","ACCESS_UNKNOWN");
  if(response.status===429)return observation(repo,"authorized","RATE_LIMITED");
  if(!response.ok)return observation(repo,"authorized","SOURCE_HTTP_FAILED");
  let meta;
  try{meta=await response.json();}
  catch{return observation(repo,"authorized","SOURCE_INVALID_RESPONSE");}
  if(typeof meta?.full_name!=="string"||
     meta.full_name.toLowerCase()!==repo.toLowerCase())
    return observation(repo,"authorized","SOURCE_INVALID_RESPONSE");
  const branches=[],branchHeads=Object.create(null);
  let complete=false;
  for(let page=1;page<=maxPages;page++){
    const result=await request(url+"/branches?per_page=100&page="+page);
    if(!result)return observation(repo,"authorized","PARTIAL_UNAVAILABLE",{branches,branchHeads});
    if(result.status===401||result.status===403||result.status===404)
      return observation(repo,"authorized","PARTIAL_ACCESS_DENIED",{branches,branchHeads});
    if(result.status===429)return observation(repo,"authorized","PARTIAL_RATE_LIMITED",{branches,branchHeads});
    if(!result.ok)return observation(repo,"authorized","PARTIAL_HTTP_FAILED",{branches,branchHeads});
    let batch;
    try{batch=await result.json();}
    catch{return observation(repo,"authorized","PARTIAL_INVALID",{branches,branchHeads});}
    if(!Array.isArray(batch))return observation(repo,"authorized","PARTIAL_INVALID",{branches,branchHeads});
    for(const b of batch) {
      if(!safeBranch(b?.name)||!validSha(b?.commit?.sha))
        return observation(repo,"authorized","PARTIAL_INVALID",{branches,branchHeads});
      if(Object.hasOwn(branchHeads,b.name)){
        if(branchHeads[b.name]!==b.commit.sha)
          return observation(repo,"authorized","PARTIAL_INVALID",{branches,branchHeads});
        // A repeated branch makes pagination completeness untrustworthy.
        return observation(repo,"authorized","PARTIAL_INVALID",{branches,branchHeads});
      }
      branches.push(b.name);branchHeads[b.name]=b.commit.sha;
    }
    if(batch.length<100){complete=true;break;}
  }
  return {schema:SCHEMA,scope:"repository",repository:repo,access:"authorized",
    status:complete?"OK":"PARTIAL",complete,
    repositoryId:Number.isSafeInteger(meta.id)?meta.id:null,
    defaultBranch:meta.default_branch??null,branches:[...new Set(branches)].sort(),
    branchHeads,pushedAt:meta.pushed_at??null,
    observedAt:new Date().toISOString(),
    provenance:{provider:"github",access:"authorized",
      endpoint:url,authority:"GitHub REST API"},readiness:"not_assessed"};
}
export async function observeGitHubSource(repository,{access="public",
  publicObserver,fetcher,credentialProvider,signal}={}) {
  const repo=repositoryName(repository);
  if(access==="public") {
    if(typeof publicObserver!=="function")
      return observation(repo,"public","PUBLIC_OBSERVER_REQUIRED");
    // The Atlas public observer remains its own single owner.
    const output=await publicObserver(repo);
    return {...output,sourceContract:SCHEMA};
  }
  if(access==="authorized")
    return observeAuthorizedGitHubRepo(repo,{fetcher,credentialProvider,signal});
  throw Error("SOURCE_ACCESS_MODE_INVALID");
}
