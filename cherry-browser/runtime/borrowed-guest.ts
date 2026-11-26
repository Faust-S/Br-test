// SPDX-License-Identifier: AGPL-3.0-only
// Narrow host binding for the original Cherry GuestSession; no second browser engine.
import type { WebContents } from 'electron'
import { GuestSession } from '@main/features/browser/session/GuestSession'
import { BrowserSessionError } from '@main/features/browser/session/BrowserSessionError'
import type { SessionOwnership } from '@main/features/browser/browserUse'

export class BorrowedGuestService {
  private current?: GuestSession
  private readonly owners = new Map<string, number>()
  private cleanup: Promise<void> = Promise.resolve()
  private closed = false

  constructor(private readonly guest: WebContents) {}

  async acquire(guest: WebContents, owner: string, ownership: SessionOwnership): Promise<GuestSession> {
    if (guest !== this.guest || ownership.ownership !== 'borrowed' || !owner)
      throw new BrowserSessionError('not_allowed')
    // The original service waits for debugger/WebMCP teardown before reacquisition.
    while (true) {
      const pending = this.cleanup
      await pending
      if (pending === this.cleanup) break
    }
    if (this.closed || guest.isDestroyed()) throw new BrowserSessionError('debugger_unavailable')
    if (!this.current) this.current = new GuestSession(guest, 'borrowed')
    this.owners.set(owner, (this.owners.get(owner) ?? 0) + 1)
    this.current.lastActive = Date.now()
    return this.current
  }

  release(guest: WebContents, owner: string): void {
    if (guest !== this.guest) return
    const count = this.owners.get(owner)
    if (!count) return
    if (count === 1) this.owners.delete(owner)
    else this.owners.set(owner, count - 1)
    if (!this.owners.size) this.releaseSession()
  }

  private releaseSession(): void {
    const session = this.current
    if (!session) return
    this.current = undefined
    session.dispose()
    this.cleanup = session.settleWebTools()
    // Observe rejection immediately; future acquire/close still fail closed.
    void this.cleanup.catch(() => {})
  }

  async close(): Promise<void> {
    this.closed = true
    this.owners.clear()
    this.releaseSession()
    await this.cleanup
  }
}
