// Explicit public Atlas adapter reuse; authorized observations use host credentials.
import assert from "node:assert/strict";
import {observeGitHubSource,observeAuthorizedGitHubRepo} from "../host-bindings/github-source.mjs";
import {observePublicRepo} from "../../atlas-public/src/github-public.mjs";

function reply(status,body){
  return {ok:status>=200&&status<300,status,json:async()=>body};
}
const calls=[];
const authorizedFetch=async (url,options)=>{
  calls.push({url,hasAuth:!!options.headers.Authorization});
  if(url.endsWith("?per_page=100&page=1"))return reply(200,[{name:"main",commit:{sha:"a".repeat(40)}}]);
  return reply(200,{full_name:"Faust-S/Atlas",id:456,default_branch:"main",
                    pushed_at:"2026-11-04T00:00:00Z"});
};
const tokenProvider=async()=>"SYNTHETIC-LOCAL-TEST-ONLY";
const observed=await observeAuthorizedGitHubRepo("Faust-S/Atlas",{
  fetcher:authorizedFetch,credentialProvider:tokenProvider});
assert.equal(observed.status,"OK");
assert.equal(observed.complete,true);
assert.deepEqual(observed.branches,["main"]);
assert.equal(observed.branchHeads.main,"a".repeat(40));
assert.equal(calls.length,2);
assert.ok(calls.every(c=>c.hasAuth));
assert.ok(!JSON.stringify(observed).includes("SYNTHETIC-LOCAL-TEST-ONLY"));
const inaccessible=await observeAuthorizedGitHubRepo("Faust-S/Atlas",{
  fetcher:async()=>reply(404,{}),credentialProvider:tokenProvider});
assert.equal(inaccessible.status,"ACCESS_UNKNOWN");
assert.equal(inaccessible.complete,false);
const denied=await observeAuthorizedGitHubRepo("Faust-S/Atlas",{
  fetcher:async()=>reply(403,{}),credentialProvider:tokenProvider});
assert.equal(denied.status,"ACCESS_DENIED");
const noToken=await observeAuthorizedGitHubRepo("Faust-S/Atlas",{
  fetcher:async()=>{throw Error("MUST_NOT_FETCH")},credentialProvider:async()=>null});
assert.equal(noToken.status,"AUTHORIZATION_REQUIRED");
const partial=await observeAuthorizedGitHubRepo("Faust-S/Atlas",{
  fetcher:async(url)=>url.endsWith("page=1")?reply(403,{}):
    reply(200,{full_name:"Faust-S/Atlas",id:456,default_branch:"main"}),
  credentialProvider:tokenProvider});
assert.equal(partial.status,"PARTIAL_ACCESS_DENIED");
assert.equal(partial.complete,false);
// Host-owned secret provider may fail before any request starts.
let providerFetches=0;
const unavailableCredentials=await observeAuthorizedGitHubRepo("Faust-S/Atlas",{
  credentialProvider:async()=>{throw Error("PRIVATE_HOST_CREDENTIALS_UNAVAILABLE");},
  fetcher:async()=>{providerFetches++;throw Error("MUST_NOT_REQUEST");}
});
assert.equal(unavailableCredentials.status,"AUTHORIZATION_REQUIRED");
assert.equal(providerFetches,0);
assert.ok(!JSON.stringify(unavailableCredentials).includes("PRIVATE_HOST_CREDENTIALS_UNAVAILABLE"));
// GitHub metadata must not manufacture a verified branch from malformed SHA.
for(const branches of [
  [{name:"main",commit:{sha:"not-a-sha"}}],
  [{name:"main",commit:{sha:"b".repeat(40)}},{name:"main",commit:{sha:"c".repeat(40)}}],
  [{name:"constructor",commit:{sha:"d".repeat(40)}},{name:"constructor",commit:{sha:"d".repeat(40)}}]
]){
 const invalid=await observeAuthorizedGitHubRepo("Faust-S/Atlas",{
   credentialProvider:tokenProvider,
   fetcher:async(url)=>url.includes("/branches?")
     ?reply(200,branches):reply(200,{full_name:"Faust-S/Atlas"})
 });
 assert.equal(invalid.status,"PARTIAL_INVALID");
 assert.equal(invalid.complete,false);
}
const publicObserved=await observeGitHubSource("Faust-S/Atlas",{
  access:"public",publicObserver:observePublicRepo});
assert.equal(publicObserved.complete,true);
assert.equal(publicObserved.scope,"repository");
assert.ok(typeof publicObserved.branchHeads.main==="string");
assert.equal(publicObserved.provenance.access,"public");
console.log(JSON.stringify({schema:"IWXA_GITHUB_SOURCE_QUALIFICATION/1",
  status:"PASS",public_real:true,authorized_synthetic:true,
  private_live_qualified:false,public_repository:"Faust-S/Atlas",
  branches:publicObserved.branches.length,
  access_404:"ACCESS_UNKNOWN",without_token:"AUTHORIZATION_REQUIRED",
  partial:"PARTIAL_ACCESS_DENIED",provider_error_no_request:true,
  malformed_sha_and_duplicate_branch_rejected:true}));
