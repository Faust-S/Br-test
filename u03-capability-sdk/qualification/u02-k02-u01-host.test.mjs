// Public U02 + K02 (owner-pinned) + U03 + REAL U01 git CLI.
// Test host owns approval and receipt; no Agent/Atlas/Center internals copied.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {basename,resolve} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {spawnSync} from 'node:child_process';
import {performance} from 'node:perf_hooks';
import {CliConnection} from '../../universal-install/host-bindings/program-connection.mjs';
import {createProductHostPort} from '../../universal-install/host-bindings/product-hosts.mjs';
import {assess,validateDescriptor} from '../src/capability.mjs';
import {projectU01Capabilities,observeU01CapabilitiesAsync} from '../src/u01-bridge.mjs';

const root=resolve(fileURLToPath(new URL('../../',import.meta.url)));
const k02root=resolve(root,'k02-owner-latest');
const u02root=resolve(root,'u02-owner-latest');
const gitRevision='6fd060eb3adc376505f1922bf5b43d557d4c8882';
const gitPath='modules/k02-interoperability/skills/exact-source-audit.instructions.json';
const k02=await import(pathToFileURL(resolve(k02root,'modules/k02-interoperability/index.mjs')).href);
const {GitCliProvider,GIT_INPUT,GIT_OUTPUT}=await import(pathToFileURL(resolve(k02root,
 'modules/k02-interoperability/adapters/git-cli.mjs')).href);
const u02=await import(pathToFileURL(resolve(u02root,'u02-source-connectors/source-read.mjs')).href);
const {createHostSelectedSourcePort}=await import(pathToFileURL(resolve(u02root,
 'u02-source-connectors/host-source.mjs')).href);
const {sourceChange}=await import(pathToFileURL(resolve(u02root,
 'u02-source-connectors/port.mjs')).href);

const sha = x=>createHash('sha256').update(x).digest('hex');
const id='GIT_SOURCE_U03_V1';
const operation='show';
const requirementId='selected-public-source';
const capabilityId='source.git.show';
const host={hostId:'iwxa.host.atlas',version:'0.1.0'};
const u03descriptor={schema:'IWXA_MODULE_DESCRIPTOR/1.0.0',
 id:'iwxa.u03.external.git.host',version:'0.1.0',
 provides:[{id:capabilityId,version:'1.0.0'}],
 requires:[{id:capabilityId,range:'=1.0.0'}],
 hostCompatibility:[{hostId:'iwxa.host.atlas',range:'=0.1.0'},
  {hostId:'iwxa.host.center',range:'=0.1.0'}]};
const bindings={programId:id,bindings:[{operation,id:capabilityId,version:'1.0.0'}]};
const solution={solutionId:'u03-source-and-program',requirements:[{
 requirementId,kind:'tool',capabilityId,inputSchema:GIT_INPUT,
 outputSchema:GIT_OUTPUT,effectClass:'HOST_AUTHORIZED_READ',required:true
}]};
const task={taskId:'U03-INTEGRATION-REAL-GIT',solutionId:solution.solutionId};
const spec={schema:'IWXA_CLI_PROGRAM/1',program_id:id,
 executable_name:process.platform==='win32'?'git.exe':'git',
 version_args:['--version'],version_contains:'git version',
 operations:{show:['-C',k02root,'show','{revpath}']}};
const graph={sourceReads:0,k02Probes:0,capabilityReads:0,invocations:0,connects:0};
function requireInteropCode(error,code){return error?.code===code;}
function currentTime(label,fn){const began=performance.now();return Promise.resolve(fn()).then(value=>({label,ms:performance.now()-began,value}));}

test('public source -> native K02 choice -> real U01 git -> U03 host receipt, JS + Python',async t=>{
 assert.equal(validateDescriptor(u03descriptor),true);
 const gitSource=spawnSync('git',['-C',k02root,'show',gitRevision+':'+gitPath],{
  encoding:null,timeout:10000,maxBuffer:1024*1024
 });
 assert.equal(gitSource.status,0,gitSource.stderr?.toString('utf8'));
 const original=Buffer.from(gitSource.stdout);
 // Git's committed blob is the canonical source. A Windows working-tree
 // checkout may normalize line endings; never change observed source bytes.
 let selected=Buffer.from(original);
 const source=createHostSelectedSourcePort({
  hostId:'atlas',scopeId:'workspace-u03',sourceId:'k02-instructions',bindingId:'task-actual',
  readSelected:async()=>{graph.sourceReads++;return {status:'OK',bytes:Uint8Array.from(selected)};}
 });
 const captured=await u02.captureSource(source);
 assert.equal(captured.status,'OK');
 assert.equal(captured.read.revision,captured.pin.revision);
 assert.equal(captured.read.authority,'OBSERVATION_ONLY');
 assert.equal(captured.observation.provenance.hostId,'atlas');
 assert.equal(captured.pin.observationType,'file');
 assert.equal(captured.read.contentRef,'sha256:'+sha(original));
 assert.equal(Buffer.from(captured.read.content.data,'base64').equals(original),true);

 const provider=new GitCliProvider(k02root);
 const k02Port={describeCapabilities:async()=>{graph.k02Probes++;return provider.describeCapabilities();}};
 const nativeDescriptor=await k02.describeCapabilities(k02Port);
 assert.equal(nativeDescriptor.availability,'available');
 const choice=k02.resolveRequirements(solution,[nativeDescriptor]);
 assert.equal(choice.status,'SUPPORTED');
 assert.equal(choice.selected[0].providerId,nativeDescriptor.providerId);
 const plan=k02.buildInvocationPlan(task,[],choice);
 assert.equal(plan.steps[0].invocationAuthority,'HOST_ONLY');
 assert.equal(plan.steps[0].proofOfInvoke,null);
 assert.equal(plan.steps[0].invocationState,'NOT_INVOKED');

 const connection=new CliConnection(spec);
 let hostApproved=false;
 const executor=({host:which,operation:method,request})=>{
  assert.equal(which,'atlas');
  if(method==='capabilities')graph.capabilityReads++;
  if(method==='connect')graph.connects++;
  if(method==='invoke'){
   if(!hostApproved)throw Error('HOST_APPROVAL_REQUIRED');
   graph.invocations++;
  }
  return method==='invoke'
   ?connection.invoke(request.operation,request.params)
   :connection[method]();
 };
 const owner=createProductHostPort('atlas',executor);
 assert.equal((await owner.inspect({})).status,'DISCOVERED');
 assert.equal(graph.invocations,0,'program discovery must never execute its operation');
 assert.equal((await owner.connect({})).status,'CONNECTED');
 const first=await owner.capabilities({});
 const firstStatuses=projectU01Capabilities(first,bindings);
 assert.equal(firstStatuses[0].reason,'operation-unverified');
 assert.deepEqual(assess(u03descriptor,host,firstStatuses).reasons,
  ['U03_CAPABILITY_UNAVAILABLE:'+capabilityId]);
 assert.equal(graph.invocations,0);
 const preflight=await k02.verifyInvocationReadiness(plan,requirementId,k02Port,plan.planDigest);
 assert.equal(preflight.status,'MATCHED_NOT_AUTHORIZED');
 assert.equal(graph.invocations,0,'K02 must not invoke U01');
 await assert.rejects(()=>owner.invoke({operation,params:{revpath:gitRevision+':'+gitPath}}),
  /HOST_APPROVAL_REQUIRED/);
 assert.equal(graph.invocations,0);

 // Revision changes between discovery and approval: U02 rechecks the pinned bytes.
 selected=Buffer.from(original.toString('utf8')+'\nchanged source revision\n');
 const changed=await source.refresh();
 assert.equal(sourceChange(captured.observation,changed).status,'CHANGED');
 const changedRead=await u02.readPinnedSource(source,captured.pin);
 assert.equal(changedRead.status,'REVISION_MISMATCH');
 assert.equal(graph.invocations,0,'stale source must not trigger an external operation');
 selected=Buffer.from(original);
 const fresh=await u02.readPinnedSource(source,captured.pin);
 assert.equal(fresh.status,'OK');
 assert.equal(fresh.contentRef,captured.read.contentRef);
 assert.equal(graph.invocations,0);

 const input={revision:gitRevision,path:gitPath};
 k02.validateInvocationInput(plan.steps[0],input);
 // Only the simulated owning host approves the operation; U03/K02 do not.
 hostApproved=true;
 assert.equal(hostApproved,true);
 const invocation=await currentTime('real-git-invoke',()=>owner.invoke({
  operation,params:{revpath:input.revision+':'+input.path}
 }));
 const executed=invocation.value;
 assert.equal(executed.status,'EXECUTED');
 assert.equal(executed.executed,true);
 assert.equal(graph.invocations,1,'exactly one JS U01 operation');
 assert.equal(Buffer.from(executed.output,'utf8').equals(original),true);
 const output={sourceRef:'git:'+input.revision+':'+input.path,
  revision:input.revision,content:executed.output,sha256:sha(executed.output)};
 k02.validateInvocationOutput(plan.steps[0],output);
 assert.equal(output.sha256,sha(original));
 // A real observed U01 execution is not itself an owner-issued receipt.
 const verifiedSnapshot=await owner.capabilities({});
 assert.equal(verifiedSnapshot.operations[0].verified,true);
 assert.equal(projectU01Capabilities(verifiedSnapshot,bindings)[0].reason,'execution-receipt-missing');
 const hostReceiptRef='qualification:atlas:sha256:'+sha(JSON.stringify({
  programId:id,operation,epoch:executed.connection_epoch,planDigest:plan.planDigest,
  sourceRevision:captured.pin.revision,outputSha256:output.sha256}));
 const verifiedBindings={...bindings,receipts:{show:{
  observation:executed,observationRef:hostReceiptRef
 }}};
 const qualified=projectU01Capabilities(verifiedSnapshot,verifiedBindings);
 assert.equal(qualified[0].available,true);
 assert.equal(qualified[0].observationRef,hostReceiptRef);
 assert.equal(assess(u03descriptor,host,qualified).compatible,true);
 assert.equal(assess(u03descriptor,{...host,version:'0.2.0'},qualified).compatible,false);
 assert.deepEqual(assess({...u03descriptor,requires:[{id:'missing.capability',range:'=1.0.0'}]},
  host,qualified).reasons,['U03_CAPABILITY_MISSING:missing.capability']);
 const centerHost={hostId:'iwxa.host.center',version:'0.1.0'};
 assert.equal(assess(u03descriptor,centerHost,qualified).compatible,true,
  'same portable U03 descriptor works with independent Center host identity');

 // Read the evidence twice without calling U01.invoke, preserving operation identity.
 const oldInvocations=graph.invocations;
 const again1=projectU01Capabilities(verifiedSnapshot,verifiedBindings);
 const again2=projectU01Capabilities(verifiedSnapshot,verifiedBindings);
 assert.deepEqual(again1,again2);
 assert.equal((await u02.readPinnedSource(source,captured.pin)).status,'OK');
 assert.equal(graph.invocations,oldInvocations);

 // U03 compatibility must not override unavailable K02 provider/tool.
 assert.equal(k02.resolveRequirements(solution,[{...nativeDescriptor,
  capabilities:[]}]).status,'UNSUPPORTED');
 assert.equal(k02.resolveRequirements(solution,[{...nativeDescriptor,
  availability:'unavailable',unavailableReason:'GIT_NOT_RUNNING'}]).status,'UNSUPPORTED');
 await assert.rejects(()=>k02.verifyInvocationReadiness(plan,requirementId,{
  describeCapabilities:async()=>({...nativeDescriptor,contractVersion:'IWXA.K02.INTEROP/99.0'})
 },plan.planDigest),e=>requireInteropCode(e,'CONTRACT_VERSION_MISMATCH'));
 await assert.rejects(()=>k02.verifyInvocationReadiness(plan,requirementId,{
  describeCapabilities:async()=>({...nativeDescriptor,availability:'unavailable',
   unavailableReason:'GIT_UNAVAILABLE'})
 },plan.planDigest),e=>requireInteropCode(e,'PROVIDER_UNAVAILABLE'));
 // A changed U02 observation does not update K02's plan automatically.
 const forged=structuredClone(verifiedBindings);
 forged.receipts.show.observation.connection_epoch='changed-epoch';
 assert.equal(projectU01Capabilities(verifiedSnapshot,forged)[0].available,false);

 // Cross-language consumer verifies native K02 digest, U02 source, and independently
 // invokes the existing Python U01 CLI. There is no Python copy of K02's resolver.
 const vector={programId:id,gitRevision,gitPath,repoPath:k02root,
  source:{descriptor:captured.descriptor,observation:captured.observation,
   pin:captured.pin,read:{revision:captured.read.revision,contentRef:captured.read.contentRef}},
  plan,moduleDescriptor:u03descriptor,host,
  expectedSha256:output.sha256};
 const py=spawnSync(process.env.PYTHON??(process.platform==='win32'?'python':'python3'),
  [resolve(root,'u03-capability-sdk/qualification/u02_k02_u01_python_consumer.py')],
  {encoding:'utf8',input:JSON.stringify(vector),timeout:50000});
 assert.equal(py.status,0,py.stderr||py.error?.message);
 const pythonResult=JSON.parse(py.stdout.trim());
 assert.equal(pythonResult.status,'PASS');
 assert.equal(pythonResult.resultSha256,output.sha256);
 assert.equal(pythonResult.invocations,1);
 assert.equal(graph.invocations,1,'Python consumer uses a separate owning U01 connection');

 // Reconnection revokes stale U03 receipts. Never replay without new owner approval.
 assert.equal((await owner.disconnect({})).status,'DISCONNECTED');
 assert.equal((await owner.recover({})).status,'CONNECTED');
 const postRestart=await owner.capabilities({});
 assert.notEqual(postRestart.connection_epoch,executed.connection_epoch);
 assert.equal(projectU01Capabilities(postRestart,verifiedBindings)[0].available,false);
 assert.equal(graph.invocations,1);

 // Compare identical two-view use cases: naive requery vs U03 reused owner snapshot.
 const iterations=300;
 const measure=async reuse=>{
  const starts=graph.capabilityReads,begin=performance.now();
  for(let n=0;n<iterations;n++){
   if(reuse){
    const observed=await owner.capabilities({});
    projectU01Capabilities(observed,verifiedBindings);
    projectU01Capabilities(observed,verifiedBindings);
   }else{
    await observeU01CapabilitiesAsync(owner,verifiedBindings);
    await observeU01CapabilitiesAsync(owner,verifiedBindings);
   }
  }
  return {ms:+(performance.now()-begin).toFixed(3),
   capabilityReads:graph.capabilityReads-starts,invocations:graph.invocations};
 };
 const before=await measure(false),after=await measure(true);
 assert.equal(before.capabilityReads,iterations*2);
 assert.equal(after.capabilityReads,iterations);
 assert.equal(after.invocations,1);
 const report={status:'PASS',schema:'IWXA_U03_OWNER_INTEROP_QUALIFICATION/1',
  owners:{u02:'ac587cc48f70e8547c4e162509fa12f89257e5b3',
   k02:gitRevision,u03:'37eddb413bae949a165ce0380f1c74199e0cad91'},
  script:'native U02 source + K02 GitCliProvider + U01 real git + U03 receipt',
  jsInvocations:graph.invocations,pythonInvocations:pythonResult.invocations,
  outputSha256:output.sha256,sourceRevision:captured.pin.revision,
  measured:{iterations,baseline:before,reusedSnapshot:after,
    oneInvokeMs:+invocation.ms.toFixed(3)},
  notes:['before/after wall-clock is environment-specific; compare same-OS only',
   'K02 and U03 do not grant execution','only public repositories used']};
 console.log(JSON.stringify(report));
 await t.test('provenance is observation only and does not confer execution authority',()=>{
  assert.equal(captured.read.authority,'OBSERVATION_ONLY');
  assert.equal(preflight.status,'MATCHED_NOT_AUTHORIZED');
  assert.equal(graph.invocations,1);
 });
});
