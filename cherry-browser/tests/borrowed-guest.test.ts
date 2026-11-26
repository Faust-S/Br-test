// SPDX-License-Identifier: AGPL-3.0-only
import { describe, it, expect, vi } from 'vitest'
import { BorrowedGuestService } from '../runtime/borrowed-guest'
import { GuestSession } from '@main/features/browser/session/GuestSession'
import { createGuest } from '../vendor/cherry-studio/src/main/features/browser/__tests__/guestFixture'

describe('IWXA host binding of the original borrowed GuestSession', () => {
  it('shares one guest between leases and does not close the user-owned page', async () => {
    const {guest} = createGuest()
    const service = new BorrowedGuestService(guest)
    const first = await service.acquire(guest,'owner',{ownership:'borrowed'})
    const second = await service.acquire(guest,'owner',{ownership:'borrowed'})
    expect(first).toBeInstanceOf(GuestSession)
    expect(second).toBe(first)
    const dispose = vi.spyOn(first,'dispose')
    service.release(guest,'owner')
    expect(dispose).not.toHaveBeenCalled()
    service.release(guest,'owner')
    expect(dispose).toHaveBeenCalledTimes(1)
    await service.close()
    expect(guest.isDestroyed()).toBe(false)
  })
  it('rejects foreign guests and managed ownership', async () => {
    const {guest} = createGuest()
    const other = createGuest().guest
    const service = new BorrowedGuestService(guest)
    await expect(service.acquire(other,'owner',{ownership:'borrowed'})).rejects.toThrow('not_allowed')
    await expect(service.acquire(guest,'owner',{ownership:'managed',close:()=>{}})).rejects.toThrow('not_allowed')
    await service.close()
    await expect(service.acquire(guest,'owner',{ownership:'borrowed'})).rejects.toThrow('debugger_unavailable')
  })
  it('rechecks cleanup if release races with an acquire continuation', async () => {
    const {guest} = createGuest()
    const service = new BorrowedGuestService(guest)
    const old = await service.acquire(guest,'a',{ownership:'borrowed'})
    let resolve!: ()=>void
    const cleanup = new Promise<void>(done=>{resolve=done})
    vi.spyOn(old,'settleWebTools').mockReturnValue(cleanup)
    const next = service.acquire(guest,'b',{ownership:'borrowed'})
    service.release(guest,'a')
    let complete = false
    void next.then(()=>{complete=true})
    await Promise.resolve()
    await Promise.resolve()
    expect(complete).toBe(false)
    resolve()
    const fresh = await next
    expect(fresh).not.toBe(old)
    service.release(guest,'b')
    await service.close()
  })
})
