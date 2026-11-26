// SPDX-License-Identifier: AGPL-3.0-only
// Adapter of the original visible-session controller to a guest created by the IWXA host.
import { randomUUID } from 'node:crypto'
import type { WebContents } from 'electron'
import { BrowserCursor } from '@main/features/browser/BrowserCursor'
import { BrowserControlSession } from '@main/features/browser/BrowserControlSession'
import { SessionBrowserController } from '@main/features/browser/mcp/SessionBrowserController'
import { BrowserSessionError } from '@main/features/browser/session/BrowserSessionError'
import { sessionToolDefinitions } from '@main/ai/mcp/browserToolDefinitions'
import { toolHandlers } from '@main/features/browser/mcp/tools/registry'
import { normalizeBrowserUrl } from '@shared/utils/browserUrl'
import type { BrowserCursorArrival, BrowserCursorState } from '@shared/types/browserCursor'
import type { BrowserGuestTarget } from '@main/features/browser/BrowserGuestRegistry'
import type { CallToolResult } from '@modelcontextprotocol/server'
import { BorrowedGuestService } from './borrowed-guest'
import { ControlLifetime } from './control-lifetime.mjs'

export interface VisibleBrowserHost {
  // Supplied only by the trusted host, never by website content or MCP tool args.
  sessionId: string
  guest: WebContents
  isFocused(): boolean
  reveal(): void
  cursor(state: BrowserCursorState): void
  controlChanged?(): void
  allowNavigation(url: string): boolean
  authorize(tool: string, args: unknown): boolean
}

class VisibleController extends SessionBrowserController {
  constructor(service: BorrowedGuestService, binding: ConstructorParameters<typeof SessionBrowserController>[1],
    private readonly allowed: (url: string) => boolean) {
    // Only the original acquire/release API is consumed by SessionBrowserController.
    // The wider Cherry application service is intentionally not loaded.
    super(service as unknown as ConstructorParameters<typeof SessionBrowserController>[0], binding)
  }
  override validateUrl(raw: string): string {
    const url = normalizeBrowserUrl(raw)
    if (!this.allowed(url)) throw new BrowserSessionError('not_allowed')
    return url
  }
}

export class VisibleBrowserAdapter {
  readonly tabId = randomUUID()
  private readonly cursor: BrowserCursor
  private readonly target: BrowserGuestTarget
  private readonly service: BorrowedGuestService
  private readonly lifetime = new ControlLifetime<BrowserControlSession>()
  private readonly debuggerHandle: WebContents['debugger']
  private presented = false
  private destroyed = false

  constructor(private readonly host: VisibleBrowserHost) {
    if (!host.sessionId || host.guest.isDestroyed()) throw new BrowserSessionError('not_allowed')
    this.service = new BorrowedGuestService(host.guest)
    const abort = new AbortController()
    this.cursor = new BrowserCursor(
      { sessionId: host.sessionId, tabId: this.tabId, scope: 'agent' },
      state => { try { host.cursor(state) } catch { /* UI may already be gone */ } }, () => host.isFocused(), () => host.guest.getZoomFactor()
    )
    this.target = {
      ownerId: host.sessionId, sessionId: host.sessionId, tabId: this.tabId, guest: host.guest,
      // Window identity is established by the host, not used by the original controller.
      windowId: host.sessionId as BrowserGuestTarget['windowId'], abort, cursor: this.cursor,
      beginExecution: () => ({ dispose() {} }),
      dispose: () => abort.abort(new BrowserSessionError('debugger_unavailable'))
    }
    host.guest.on('did-start-navigation', this.onNavigation)
    this.debuggerHandle = host.guest.debugger
    this.debuggerHandle.on('detach', this.onDetach)
    host.guest.once('destroyed', this.onDestroyed)
  }

  private onNavigation = (event: { isMainFrame: boolean; isSameDocument: boolean }) => {
    if (event.isMainFrame && !event.isSameDocument) this.cursor.hide(new BrowserSessionError('stale_ref'))
  }
  private onDetach = () => this.cursor.hide(new BrowserSessionError('stale_ref'))
  private onDestroyed = () => { void this.dispose().catch(() => {}) }

  get agentEnabled(): boolean { return this.lifetime.enabled }
  private assertAvailable(): void {
    if (!this.lifetime.enabled || !this.presented || this.destroyed || this.host.guest.isDestroyed())
      throw new BrowserSessionError('not_allowed')
  }
  async setPresented(value: boolean): Promise<void> {
    this.presented = value
    this.cursor.setPresented(value)
    if (!value) await this.setAgentEnabled(false)
  }

  // Only a trusted UI / host may invoke this. Revoke first, then wait for active and queued calls.
  async setAgentEnabled(value: boolean): Promise<void> {
    if (value && (!this.presented || this.destroyed || this.host.guest.isDestroyed()))
      throw new BrowserSessionError('not_allowed')
    const changed = this.lifetime.setEnabled(value, () => {
      const controller = new VisibleController(this.service, {
        assertAvailable: () => this.assertAvailable(),
        get: () => this.presented ? this.target : undefined,
        ensureGuest: async signal => {
          signal.throwIfAborted()
          this.assertAvailable()
          return this.target
        },
        focusInput: async (target, options) => {
          options.signal?.throwIfAborted()
          this.assertAvailable()
          if (target !== this.target || !this.host.isFocused())
            throw new BrowserSessionError('not_allowed')
          this.host.guest.focus()
        },
        reveal: target => {
          this.assertAvailable()
          if (target !== this.target) throw new BrowserSessionError('not_found')
          this.host.reveal()
        }
      }, url => this.host.allowNavigation(url))
      return new BrowserControlSession(() => {}, controller, true)
    })
    if (!value) this.cursor.hide()
    this.host.controlChanged?.()
    await changed
    this.host.controlChanged?.()
  }

  arrive(value: BrowserCursorArrival): void { this.cursor.arrive(value) }
  hideCursor(): void { this.cursor.hide() }
  notePopupBlocked(): void { this.target.popupBlocked = true }

  async callTool(name: string, args: unknown, signal: AbortSignal): Promise<CallToolResult> {
    this.assertAvailable()
    signal.throwIfAborted()
    const ticket = this.lifetime.capture()
    const definition = sessionToolDefinitions.find(tool => tool.name === name)
    if (!definition || !Object.hasOwn(toolHandlers, name)) throw new BrowserSessionError('not_allowed')
    const parsed = definition.inputSchema.parse(args)
    if ('tabId' in parsed && parsed.tabId !== undefined && parsed.tabId !== this.tabId)
      throw new BrowserSessionError('not_found')
    if ('privateMode' in parsed && parsed.privateMode === true ||
        'newTab' in parsed && parsed.newTab === true) throw new BrowserSessionError('not_allowed')
    const result = await ticket.control.run((controller, callSignal) => {
      // Original BrowserControlSession serializes the live-pane operations. Check current host
      // permission again when the operation leaves that queue.
      this.assertAvailable()
      callSignal.throwIfAborted()
      if (!this.lifetime.isCurrent(ticket) || !this.host.authorize(name, parsed))
        throw new BrowserSessionError('not_allowed')
      return toolHandlers[name](controller, parsed, callSignal)
    }, signal)
    if (epoch !== this.epoch) throw new Error('browser_control_interrupted: inspect the visible page before any retry')
    return result
  }

  private disposal?: Promise<void>
  dispose(): Promise<void> {
    if (this.disposal) return this.disposal
    this.destroyed = true
    this.presented = false
    this.target.dispose()
    this.cursor.dispose()
    this.host.guest.removeListener('did-start-navigation', this.onNavigation)
    this.debuggerHandle.removeListener('detach', this.onDetach)
    this.host.guest.removeListener('destroyed', this.onDestroyed)
    this.disposal = (async () => {
      try { await this.lifetime.dispose() }
      finally { await this.service.close() }
    })()
    return this.disposal
  }
}
