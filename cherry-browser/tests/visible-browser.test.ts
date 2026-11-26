// SPDX-License-Identifier: AGPL-3.0-only
import { describe, it, expect, vi } from 'vitest'
import { VisibleBrowserAdapter } from '../runtime/visible-browser'
import { createGuest } from '../vendor/cherry-studio/src/main/features/browser/__tests__/guestFixture'
function fixture() {
  const { guest, mock } = createGuest()
  Object.assign(mock,{getZoomFactor:()=>1,focus:vi.fn()})
  const authorize = vi.fn(()=>true)
  const browser = new VisibleBrowserAdapter({
    sessionId:'workspace-conversation',guest,isFocused:()=>true,
    reveal:vi.fn(),cursor:vi.fn(),allowNavigation:()=>true,authorize
  })
  const signal = new AbortController().signal
  return {browser,guest,mock,signal,authorize}
}
describe('shared browser port',()=>{
  it('lists the actual borrowed tab after explicit grant and preserves it on takeover',async()=>{
    const {browser,guest,mock,signal}=fixture()
    try {
      await expect(browser.callTool('list_tabs',{},signal)).rejects.toThrow('not_allowed')
      await browser.setPresented(true)
      await browser.setAgentEnabled(true)
      const result=await browser.callTool('list_tabs',{},signal)
      expect(result.isError).toBe(false)
      expect(JSON.parse((result.content[0] as {text:string}).text)).toEqual([
        {tabId:browser.tabId,url:'https://example.com/',title:'Test page'}
      ])
      await browser.setAgentEnabled(false)
      expect(guest.isDestroyed()).toBe(false)
      expect(mock.close).not.toHaveBeenCalled()
      await expect(browser.callTool('list_tabs',{},signal)).rejects.toThrow('not_allowed')
      await browser.setAgentEnabled(true)
      expect(browser.agentEnabled).toBe(true)
    } finally {await browser.dispose()}
    expect(mock.close).not.toHaveBeenCalled()
  })
  it('applies the host decision to MCP and original direct automation',async()=>{
    const {browser,authorize,signal}=fixture()
    try {
      await browser.setPresented(true);await browser.setAgentEnabled(true)
      authorize.mockReturnValue(false)
      await expect(browser.callTool('list_tabs',{},signal)).rejects.toThrow('not_allowed')
      await expect(browser.execute('evaluate',{code:'1'},browser.tabId,signal)).rejects.toThrow('not_allowed')
      expect(authorize).toHaveBeenCalledWith('execute',{code:'1'})
      await expect(browser.execute('getTab',{},'other',signal)).rejects.toThrow('not_found')
      await expect(browser.callTool('snapshot',{tabId:'other'},signal)).rejects.toThrow('not_found')
      await expect(browser.callTool('reset',{},signal)).rejects.toThrow('not_allowed')
    } finally {await browser.dispose()}
  })
  it('closing the pane revokes the grant without closing the guest',async()=>{
    const {browser,guest,signal}=fixture()
    try {
      await browser.setPresented(true); await browser.setAgentEnabled(true)
      await browser.setPresented(false)
      expect(guest.isDestroyed()).toBe(false)
      expect(browser.agentEnabled).toBe(false)
      await expect(browser.callTool('list_tabs',{},signal)).rejects.toThrow('not_allowed')
      await expect(browser.setAgentEnabled(true)).rejects.toThrow('not_allowed')
    } finally {await browser.dispose()}
  })
})
