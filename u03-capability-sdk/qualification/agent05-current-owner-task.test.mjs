// Agent 0.5 integration qualification using CURRENT public U02/K02 owners:
// selected two-file Task context -> K02 tool choice -> existing U01 CLI -> U03.
// A real SHA-pinned jq is supplied by the existing public U01 installer job.
// This test never imports private Agent or creates a second Task/ToolRuntime.
import assert from 'node:assert/strict';
import test from 'node:test';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,basename,resolve} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {performance} from 'node:perf_hooks';
import {CliConnection} from '../../universal-install/host-bindings/program-connection.mjs';
import {projectU01Capabilities} from '../src/u01-bridge.mjs';
import {projectProgramCard} from '../src/program-card.mjs';
import {assess} from '../src/capability.mjs';

const root=resolve(fileURLToPath(new URL('../../',import.meta.url)));
const u02root=resolve(root,'u02-owner-latest','u02-source-connectors');
const k02root=resolve(root,'k02-owner-latest','modules/k02-interoperability');
const {createLocalFileSourcePort}=await import(pathToFileURL(join(u02root,'local-file.mjs')).href);
const {prepareSelectedTaskSources}=await import(pathToFileURL(join(u02root,'selected-task-inputs.mjs')).href);
const {inspectSelectedProjectSources}=await import(pathToFileURL(join(u02root,'selected-project.mjs')).href);
const k02=await import(pathToFileURL(join(k02root,'index.mjs')).href);
const {JqCalculationCliProvider,CALCULATION_INPUT,CALCULATION_OUTPUT}=await import(
  pathToFileURL(join(k02root,'adapters/jq-calculation-cli.mjs')).href);
const sha=x=>createHash('sha256').update(x).digest('hex');
const programId='JQ_AGENT05_V1',operation='calculate',capabilityId='invoice.calculate';
const host={hostId:'iwxa.agent',version:'0.5.0'};
const moduleDescriptor={schema:'IWXA_MODULE_DESCRIPTOR/1.0.0',
  id:'iwxa.u03.agent05.invoice',version:'0.1.0',
  provides:[{id:'iwxa.program.discovery',version:'1.0.0'},
    {id:capabilityId,version:'1.0.0'}],
  requires:[{id:capabilityId,range:'=1.0.0'}],
  hostCompatibility:[{hostId:'iwxa.agent',range:'=0.5.0'}]};
const channels=[{id:'iwxa.program.discovery',kind:'DISCOVERY'},
  {id:capabilityId,kind:'OPERATION',operation}];
const config={programId,bindings:[{id:capabilityId,operation,version:'1.0.0'}]};
const orderInput={orders:[
  {documentId:'INV-1042',quantity:12,unitPriceCents:12500,discountBps:1000,shippingCents:6000},
  {documentId:'INV-1043',quantity:7,unitPriceCents:4500,discountBps:0,shippingCents:2000}
]};
const requirementId='calculate';
const solution={solutionId:'u03-agent05-invoice-demo',requirements:[{
  requirementId,kind:'tool',capabilityId,inputSchema:CALCULATION_INPUT,
  outputSchema:CALCULATION_OUTPUT,effectClass:'PURE_COMPUTE',required:true
}]};
// A test-host executable recipe, NOT a new production owner or K02 executor.
// Independent implementation using K02's declared input/output schemas.
const calculationFilter='($orders | map((.quantity * .unitPriceCents) as $subtotal | (($subtotal * .discountBps / 10000 + 0.5) | floor) as $discount | {documentId, subtotalCents:$subtotal, discountCents:$discount, totalCents:($subtotal - $discount + .shippingCents)})) as $lines | {lines:$lines, grandTotalCents:($lines | map(.totalCents) | add)}';
const executable=process.env.U03_REAL_JQ_EXECUTABLE;
const stamp=x=>Number(x.toFixed(3));

test('current U02 Task sources + current K02 choice + actual U01 jq + U03 evidence; Python parity',async()=>{
  assert.equal(typeof executable,'string','existing pinned U01 installer must supply real jq');
  const path=resolve(executable);
  const probe=spawnSync(path,['--version'],{encoding:'utf8',timeout:4000});
  assert.equal(probe.status,0,probe.stderr);
  assert.equal(probe.stdout.trim(),'jq-1.8.1','exact official pinned jq from U01 install');
  const dir=await mkdtemp(join(tmpdir(),'u03-agent05-owner-task-'));
  const counters={invoke:0,capabilities:0,sourcePinnedReads:0};
  const started=performance.now();
  try{
    await writeFile(join(dir,'orders.json'),JSON.stringify(orderInput));
    await writeFile(join(dir,'instructions.md'),
      'Invoice task: compute exact totals, preserve selected source revision; review required.\n');
    const sources=()=>[
      {key:'orders',format:'json',port:createLocalFileSourcePort({
        root:dir,rootId:'u03_task_root',relativePath:'orders.json'})},
      {key:'instructions',format:'markdown',port:createLocalFileSourcePort({
        root:dir,rootId:'u03_task_root',relativePath:'instructions.md'})}
    ];
    const sourceStart=performance.now();
    const selected=await prepareSelectedTaskSources({scopeId:'agent05_invoice',sources:sources()});
    const sourceMs=stamp(performance.now()-sourceStart);
    assert.equal(selected.schema,'IWXA_U02_TASK_INPUTS/1');
    assert.equal(selected.ready,true);
    assert.equal(selected.authority,'OBSERVATION_ONLY');
    assert.equal(selected.observations.length,2);
    assert.equal(selected.metrics.read.pinnedReadCalls,2);
    assert.equal(selected.metrics.revalidation.pinnedReadCalls,0);
    counters.sourcePinnedReads+=2;
    const parsed=JSON.parse(selected.observations.find(x=>x.key==='orders').text);
    assert.deepEqual(parsed,orderInput);
    assert.equal(selected.checkpoint.sources.length,2);
    assert.equal(JSON.stringify(selected.checkpoint).includes('INV-1042'),false,
      'persisted checkpoint must not become a second source storage');
    const provider=new JqCalculationCliProvider({executable:path});
    const selectionStart=performance.now();
    const descriptor=await k02.describeCapabilities(provider);
    assert.equal(descriptor.availability,'available');
    const choice=k02.resolveRequirements(solution,[descriptor]);
    assert.equal(choice.status,'SUPPORTED');
    const plan=k02.buildInvocationPlan(
      {taskId:'AGENT05-INVOICE-SOURCE-001',solutionId:solution.solutionId},[],choice);
    assert.equal(plan.steps.length,1);
    assert.equal(plan.steps[0].invocationAuthority,'HOST_ONLY');
    assert.equal(plan.steps[0].proofOfInvoke,null);
    assert.equal(plan.steps[0].invocationState,'NOT_INVOKED');
    const preflight=await k02.verifyInvocationReadiness(plan,requirementId,provider,plan.planDigest);
    assert.equal(preflight.status,'MATCHED_NOT_AUTHORIZED');
    k02.validateInvocationInput(plan.steps[0],parsed);
    const k02Ms=stamp(performance.now()-selectionStart);
    const spec={schema:'IWXA_CLI_PROGRAM/1',program_id:programId,
      executable_name:basename(path),version_args:['--version'],
      version_contains:'jq-1.8.1',
      operations:{[operation]:['-nc','--argjson','orders','{orders}','{filter}']}};
    const connection=new CliConnection(spec,path);
    assert.equal(connection.inspect().status,'DISCOVERED');
    assert.equal(connection.connect().status,'CONNECTED');
    const getStatus=()=>{
      counters.capabilities++;
      return connection.capabilities();
    };
    const first=getStatus();
    const firstStatus=projectU01Capabilities(first,config);
    assert.equal(firstStatus[0].reason,'operation-unverified');
    const before=projectProgramCard({programId,descriptor:moduleDescriptor,channels,
      u01:{inspect:connection.inspect(),capabilities:first}});
    const initial=before.capabilities.find(x=>x.kind==='OPERATION');
    assert.equal(initial.state,'INTEGRATED');
    assert.equal(initial.canOfferInvocation,true);
    assert.equal(initial.firstUseCandidate,true);
    assert.equal(initial.executionConfirmed,false);
    assert.equal(assess(moduleDescriptor,host,firstStatus).compatible,false);
    assert.equal(counters.invoke,0);

    // Confirm no selected source has changed since the Task input stage.
    // The low-cost U02 check never reads content or invokes a program.
    await writeFile(join(dir,'orders.json'),JSON.stringify({
      orders:[{...orderInput.orders[0],quantity:999}]}));
    const stale=await inspectSelectedProjectSources({scopeId:'agent05_invoice',
      sources:sources(),previous:selected.checkpoint});
    assert.equal(stale.sources.find(x=>x.key==='orders').freshness,'STALE');
    assert.equal(stale.metrics.pinnedReadCalls,0);
    assert.equal(stale.evidenceReady,false);
    assert.equal(counters.invoke,0);
    await writeFile(join(dir,'orders.json'),JSON.stringify(orderInput));
    const current=await inspectSelectedProjectSources({scopeId:'agent05_invoice',
      sources:sources(),previous:selected.checkpoint});
    assert.equal(current.sources.every(x=>x.freshness==='CURRENT'),true);
    assert.equal(current.metrics.pinnedReadCalls,0);
    assert.equal(counters.invoke,0);
    const blockedProvider={...descriptor,availability:'unavailable',
      unavailableReason:'JQ_NOT_AVAILABLE'};
    assert.equal(k02.resolveRequirements(solution,[blockedProvider]).status,'UNSUPPORTED');
    assert.equal(assess(moduleDescriptor,{...host,version:'0.4.0'},firstStatus).compatible,false);

    // Only this explicit test host may approve the U01 operation. K02 and U03
    // have not executed any user task and cannot grant execution authority.
    let approved=false;
    const run=(params)=>{
      if(!approved)throw Error('HOST_APPROVAL_REQUIRED');
      counters.invoke++;
      return connection.invoke(operation,params);
    };
    assert.throws(()=>run({orders:JSON.stringify(parsed),filter:calculationFilter}),
      /HOST_APPROVAL_REQUIRED/);
    assert.equal(counters.invoke,0);
    approved=true;
    const invokeStart=performance.now();
    const receipt=run({orders:JSON.stringify(parsed),filter:calculationFilter});
    const invocationMs=stamp(performance.now()-invokeStart);
    assert.equal(receipt.status,'EXECUTED');
    assert.equal(receipt.executed,true);
    assert.equal(receipt.operation,operation);
    assert.equal(counters.invoke,1);
    const result=JSON.parse(receipt.output);
    k02.validateInvocationOutput(plan.steps[0],result);
    assert.equal(result.grandTotalCents,174500);
    assert.deepEqual(result.lines.map(x=>x.totalCents),[141000,33500]);

    const post=getStatus();
    assert.equal(post.operations[0].verified,true);
    const missingReceipt=projectU01Capabilities(post,config);
    assert.equal(missingReceipt[0].reason,'execution-receipt-missing');
    const notYet=projectProgramCard({programId,descriptor:moduleDescriptor,channels,
      u01:{inspect:connection.inspect(),capabilities:post}});
    assert.equal(notYet.capabilities.find(x=>x.kind==='OPERATION').executionConfirmed,false);
    assert.equal(notYet.capabilities.find(x=>x.kind==='OPERATION').canOfferInvocation,true);
    const sourceRevision=selected.checkpoint.sources.find(x=>x.key==='orders').pin.revision;
    const receiptRef='u03:qualification:sha256:'+sha(JSON.stringify({
      programId,operation,connectionEpoch:receipt.connection_epoch,
      planDigest:plan.planDigest,sourceRevision,resultSha256:sha(receipt.output)
    }));
    const withReceipt={...config,receipts:{
      [operation]:{observation:receipt,observationRef:receiptRef}}};
    const qualified=projectU01Capabilities(post,withReceipt);
    assert.equal(qualified[0].available,true);
    assert.equal(assess(moduleDescriptor,host,qualified).compatible,true);
    const exactCard=projectProgramCard({programId,descriptor:moduleDescriptor,channels,
      u01:{inspect:connection.inspect(),capabilities:post,receipts:withReceipt.receipts}});
    const op=exactCard.capabilities.find(x=>x.kind==='OPERATION');
    assert.equal(op.state,'EXECUTABLE');
    assert.equal(op.executionConfirmed,true);
    assert.equal(op.observationRef,receiptRef);
    assert.equal(op.canOfferInvocation,true);
    assert.equal(exactCard.capabilities.find(x=>x.kind==='DISCOVERY').state,'AVAILABLE');
    const resultReadbackStart=performance.now();
    assert.deepEqual(projectU01Capabilities(post,withReceipt),qualified);
    assert.deepEqual(projectU01Capabilities(post,withReceipt),qualified);
    assert.equal(counters.invoke,1,'readback must not call U01.invoke again');
    const readbackMs=stamp(performance.now()-resultReadbackStart);

    // An independent Python SDK consumer validates this public host snapshot,
    // Task source revisions and output digest. It never re-runs jq.
    const vector={programId,moduleDescriptor,host,capabilities:post,
      receipts:withReceipt.receipts,source:selected.observations.map(x=>({
        key:x.key,providerId:x.providerId,sourceRef:x.sourceRef,
        revision:x.revision,contentRef:x.contentRef,authority:x.authority,
        observedAt:x.observedAt,provenance:x.provenance
      })),checkpoint:selected.checkpoint,plan:{
        planDigest:plan.planDigest,invocationAuthority:plan.steps[0].invocationAuthority,
        invocationState:plan.steps[0].invocationState,proofOfInvoke:plan.steps[0].proofOfInvoke
      },status:qualified[0],result,outputSha256:sha(receipt.output),
      expectedGrandTotalCents:174500};
    const py=spawnSync(process.env.PYTHON??(process.platform==='win32'?'python':'python3'),
      [join(root,'u03-capability-sdk/qualification/agent05_current_owner_task_python.py')],
      {encoding:'utf8',input:JSON.stringify(vector),timeout:15000});
    assert.equal(py.status,0,py.stderr||py.error?.message);
    const checked=JSON.parse(py.stdout.trim());
    assert.equal(checked.status,'PASS');
    assert.equal(checked.receiptRef,receiptRef);
    assert.equal(checked.grandTotalCents,result.grandTotalCents);
    assert.equal(checked.invocations,0);

    assert.equal(connection.disconnect().status,'DISCONNECTED');
    assert.equal(connection.recover().status,'CONNECTED');
    const afterRestart=getStatus();
    assert.notEqual(afterRestart.connection_epoch,receipt.connection_epoch);
    assert.equal(projectU01Capabilities(afterRestart,withReceipt)[0].available,false);
    assert.equal(counters.invoke,1);
    console.log(JSON.stringify({
      schema:'IWXA_U03_AGENT05_CURRENT_OWNERS/1',status:'PASS',
      u02Owner:'a66b79a474ad7ac0004513d7fb77f5c46c931cfb',
      k02Owner:'3c19fbb3ed0e7555959e47958c2d0c4365c45b3d',
      realJq:true,sourceCount:2,sourceReads:counters.sourcePinnedReads,
      sourceRefreshOnly:true,javascriptInvocations:counters.invoke,
      pythonInvocations:checked.invocations,grandTotalCents:result.grandTotalCents,
      receiptSha256:sha(receipt.output),sourceRevision,
      durationsMs:{sourceStage:sourceMs,k02Selection:k02Ms,externalInvoke:invocationMs,
        evidenceReadback:readbackMs,total:stamp(performance.now()-started)},
      installedAgentVerified:false,privateSourceUsed:false
    }));
  }finally{
    await rm(dir,{recursive:true,force:true});
  }
});
