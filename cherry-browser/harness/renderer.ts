// SPDX-License-Identifier: AGPL-3.0-only
import { BrowserCursorAnimation } from '../vendor/cherry-studio/src/renderer/components/BrowserCursorAnimation'
import type { BrowserCursorArrival, BrowserCursorState } from '@shared/types/browserCursor'
type State = { sessionId: string; tabId: string; enabled: boolean; url: string; title: string }
type Host = {
  enable(value: boolean): Promise<boolean>
  manual(op: string, url?: string): Promise<void>
  arrive(value: BrowserCursorArrival): Promise<void>
  onState(callback: (state: State) => void): void
  onCursor(callback: (state: BrowserCursorState) => void): void
}
const host = (window as unknown as { iwxaBrowser: Host }).iwxaBrowser
const byId = <T extends HTMLElement>(id: string) => document.getElementById(id) as T
const address = byId<HTMLInputElement>('address'), guest = byId('guest')
const enabled = byId<HTMLButtonElement>('enable'), takeover = byId<HTMLButtonElement>('takeover')
const mode = byId('mode'), error = byId('error')
let state: State | undefined
let pending: BrowserCursorArrival | undefined
let sequence = 0
function acknowledge() {
  const arrival = pending
  pending = undefined
  if (arrival) void host.arrive(arrival).catch(() => {})
}
const animation = new BrowserCursorAnimation(byId('cursor'), byId('sprite'), acknowledge)
function hide() { animation.hide(true); acknowledge() }
const fail = () => { error.textContent = 'Операция браузера не выполнена.' }
function run(action: () => Promise<unknown>) { error.textContent = ''; void action().catch(fail) }
host.onState(next => {
  if (state?.tabId !== next.tabId) { hide(); sequence = 0 }
  state = next
  if (document.activeElement !== address) address.value = next.url === 'about:blank' ? '' : next.url
  mode.textContent = next.enabled ? 'Совместное управление ИИ разрешено' : 'Управление у пользователя'
  enabled.disabled = next.enabled
  takeover.disabled = !next.enabled
})
host.onCursor(next => {
  if (!state || next.tabId !== state.tabId || next.sessionId !== state.sessionId || next.sequence <= sequence) return
  sequence = next.sequence
  acknowledge()
  if (next.kind === 'hidden') { animation.hide(); return }
  if (next.kind === 'move' && next.animate)
    pending = { sessionId: next.sessionId, tabId: next.tabId, sequence: next.sequence, documentId: next.documentId }
  const bounds = guest.getBoundingClientRect()
  if (document.hidden || bounds.width <= 0 || bounds.height <= 0) { hide(); return }
  animation.move(next.x * next.scale, next.y * next.scale, bounds.width, bounds.height,
    next.animate, matchMedia('(prefers-reduced-motion: reduce)').matches, next.kind === 'pressed')
})
enabled.onclick = () => run(() => host.enable(true))
takeover.onclick = () => run(() => host.enable(false))
byId<HTMLFormElement>('toolbar').onsubmit = event => { event.preventDefault(); run(() => host.manual('open', address.value.trim())) }
for (const op of ['back', 'forward', 'reload', 'stop']) byId(op).onclick = () => run(() => host.manual(op))
guest.addEventListener('did-start-navigation', hide)
window.addEventListener('blur', hide)
document.addEventListener('visibilitychange', hide)
new ResizeObserver(hide).observe(guest)
