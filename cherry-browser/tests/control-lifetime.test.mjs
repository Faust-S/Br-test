// SPDX-License-Identifier: AGPL-3.0-only
import test from 'node:test'
import assert from 'node:assert/strict'
import { ControlLifetime } from '../runtime/control-lifetime.mjs'
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => { resolve=a; reject=b }); return {promise,resolve,reject} }
const control = (close = async () => {}) => ({ close })

test('control starts disabled; capture does not create a hidden guest', () => {
  const life = new ControlLifetime()
  assert.equal(life.enabled,false)
  assert.throws(()=>life.capture(),/not_enabled/)
})
test('enable and repeated enable share the exact original controller', async () => {
  const life = new ControlLifetime(); const first = control(); let calls=0
  assert.equal(await life.setEnabled(true,()=>{calls++;return first}),true)
  const ticket=life.capture()
  assert.equal(await life.setEnabled(true,()=>{calls++;return control()}),true)
  assert.equal(calls,1); assert.equal(ticket.control,first); assert.equal(life.isCurrent(ticket),true)
  await life.dispose()
})
test('revocation precedes asynchronous cleanup and invalidates every old ticket', async () => {
  const life=new ControlLifetime(), gate=deferred(); let closes=0
  await life.setEnabled(true,()=>control(()=>{closes++;return gate.promise}))
  const ticket=life.capture(); const disabled=life.setEnabled(false)
  assert.equal(life.enabled,false); assert.equal(life.isCurrent(ticket),false); assert.throws(()=>life.capture())
  await Promise.resolve(); assert.equal(closes,1)
  gate.resolve(); assert.equal(await disabled,false)
})
test('re-enable waits for previous debugger cleanup', async () => {
  const life=new ControlLifetime(), gate=deferred(); let created=0
  await life.setEnabled(true,()=>control(()=>gate.promise))
  const old=life.capture(); const stop=life.setEnabled(false); const start=life.setEnabled(true,()=>{created++;return control()})
  await Promise.resolve(); assert.equal(created,0); assert.equal(life.enabled,false)
  gate.resolve(); await stop; assert.equal(await start,true); assert.equal(created,1); assert.equal(life.isCurrent(old),false)
  await life.dispose()
})
test('takeover supersedes queued enable', async () => {
  const life=new ControlLifetime(), gate=deferred(); let created=0
  await life.setEnabled(true,()=>control(()=>gate.promise))
  const stop=life.setEnabled(false); const start=life.setEnabled(true,()=>{created++;return control()}); const takeover=life.setEnabled(false)
  gate.resolve(); await Promise.all([stop,takeover]); assert.equal(await start,false); assert.equal(created,0); assert.equal(life.enabled,false)
})
test('rapid competing enables construct only latest controller', async () => {
  const life=new ControlLifetime(); let created=0
  const first=life.setEnabled(true,()=>{created++;return control()}); const second=life.setEnabled(true,()=>{created++;return control()})
  assert.equal(await first,false); assert.equal(await second,true); assert.equal(created,1); await life.dispose()
})
test('cleanup failure remains fail closed; no replacement controller', async () => {
  const life=new ControlLifetime(); let created=0
  await life.setEnabled(true,()=>control(async()=>{throw new Error('detach_failed')}))
  await assert.rejects(life.setEnabled(false),/detach_failed/)
  await assert.rejects(life.setEnabled(true,()=>{created++;return control()}),/detach_failed/)
  assert.equal(created,0); assert.equal(life.enabled,false)
})
test('dispose is idempotent and permanent', async () => {
  const life=new ControlLifetime(); let closes=0
  await life.setEnabled(true,()=>control(async()=>{closes++}))
  const first=life.dispose(), second=life.dispose()
  assert.equal(first,second); await first; assert.equal(closes,1)
  await assert.rejects(life.setEnabled(true,()=>control()),/closed/)
})
test('failed creation does not grant control', async () => {
  const life=new ControlLifetime(); await assert.rejects(life.setEnabled(true,()=>null),/invalid_browser_control/)
  assert.equal(life.enabled,false)
})
test('malformed UI state is rejected', async () => {
  const life=new ControlLifetime(); await assert.rejects(life.setEnabled('true',()=>control()),/invalid_control_state/)
  assert.equal(life.enabled,false)
})
