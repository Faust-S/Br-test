import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { Script, createContext, runInContext } from 'node:vm';
import { test } from 'node:test';

// Parse the shipped Rust constant itself; no duplicate diagnostic source.
const rust = readFileSync(new URL('../src-tauri/src/controller.rs', import.meta.url), 'utf8');
const probe = rust.match(/const WORKSPACE_CONFIG_PROBE:\s*&str\s*=\s*r#"([\s\S]*?)"#;/)?.[1];
assert.ok(probe, 'the debug-only Workspace probe must exist');
const script = new Script(probe, { filename:'WORKSPACE_CONFIG_PROBE' });

function context() {
  return createContext({
    location: { origin:'https://artbazi.ru', pathname:'/workspace/' },
    window: {},
    document: {
      readyState:'complete',
      body:{ textContent:'' },
      getElementsByTagName:()=>[],
      querySelector:()=>null,
      querySelectorAll:()=>[],
      getElementById:()=>null,
    },
    performance: { getEntriesByType:()=>[] },
  });
}
function evaluate(ctx) {
  return JSON.parse(JSON.stringify(script.runInContext(ctx)));
}

test('probe compiles and does not invent scheduler state when unavailable', () => {
  const output = evaluate(context()).moduleBoot.engineTasks;
  assert.equal(output.available,false);
  assert.equal(output.queuedTotal,null);
  assert.equal(output.wakePending,null);
  assert.equal(output.dynamicScriptsPending,null);
});

test('probe reports queue counters and flags without consuming user callbacks', () => {
  const ctx = context();
  runInContext(`
    const _browserPostedTaskQueues = [[],[],[11],[12,13],[],[]];
    let _browserPostedTaskWakePending = true;
    let _browserPostedTaskRetryTimer = null;
    let _browserPostedTaskRetryCount = 2;
    globalThis.__obscura_hasPendingDynamicScripts = () => false;
    globalThis.__obscura_hasPendingLoadDelayingScripts = () => true;
  `,ctx);
  const first=evaluate(ctx).moduleBoot.engineTasks;
  const second=evaluate(ctx).moduleBoot.engineTasks;
  assert.deepEqual(first.queuedByPriority,[0,0,1,2,0,0]);
  assert.equal(first.queuedTotal,3);
  assert.equal(first.available,true);
  assert.equal(first.wakePending,true);
  assert.equal(first.retryScheduled,false);
  assert.equal(first.retryCount,2);
  assert.equal(first.dynamicScriptsPending,false);
  assert.equal(first.loadDelayingScriptsPending,true);
  assert.deepEqual(second, first);
  assert.equal(runInContext('_browserPostedTaskQueues[3].length',ctx),2);
});

test('probe rejects unrelated origins before inspecting browser internals', () => {
  const ctx = context();
  ctx.location.origin='https://untrusted.example';
  assert.equal(evaluate(ctx).scope, 'outside-preview-workspace');
});
