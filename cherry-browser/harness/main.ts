// SPDX-License-Identifier: AGPL-3.0-only
// Qualification host ONLY: one original Chromium guest, no Agent clone, no private source.
import { app, BrowserWindow, ipcMain, session, type WebContents } from 'electron'
import path from 'node:path'
import { pathToFileURL, fileURLToPath } from 'node:url'
import { VisibleBrowserAdapter } from '../runtime/visible-browser'
import { serveBrowserStdio } from '../runtime/mcp-port'
import { normalizeBrowserUrl } from '@shared/utils/browserUrl'
import type { BrowserCursorArrival } from '@shared/types/browserCursor'

const partition = 'persist:iwxa-browser-qualification'
const dist = path.dirname(fileURLToPath(import.meta.url))
const chromePath = path.join(dist, 'index.html')
const chromeUrl = pathToFileURL(chromePath).href
const mcpRequested = process.argv.includes('--mcp')
app.setName('IWXA Browser Qualification')
let win: BrowserWindow | undefined
let guest: WebContents | undefined
let adapter: VisibleBrowserAdapter | undefined
let stdio: ReturnType<typeof serveBrowserStdio> | undefined
let quitting = false

function allowedUrl(raw: string): boolean {
  try { normalizeBrowserUrl(raw); return true } catch { return false }
}
function navigate(raw: string) {
  if (!guest || guest.isDestroyed()) throw new Error('browser_guest_unavailable')
  return guest.loadURL(normalizeBrowserUrl(raw))
}
function publishState() {
  if (win && !win.isDestroyed() && adapter && guest && !guest.isDestroyed())
    win.webContents.send('iwxa:browser-state', {
      sessionId: 'qualification', tabId: adapter.tabId, enabled: adapter.agentEnabled,
      url: guest.getURL(), title: guest.getTitle()
    })
}
function trusted(event: Electron.IpcMainInvokeEvent) {
  if (!win || win.isDestroyed() || event.sender !== win.webContents ||
      event.senderFrame !== win.webContents.mainFrame || event.senderFrame?.url !== chromeUrl)
    throw new Error('unverified_browser_chrome')
}
function requireAdapter(event: Electron.IpcMainInvokeEvent) {
  trusted(event)
  if (!adapter || !guest || guest.isDestroyed()) throw new Error('browser_guest_unavailable')
  return { adapter, guest }
}

async function shutdown() {
  if (quitting) return
  quitting = true
  try { await stdio?.close() } finally {
    try { await adapter?.dispose() } finally { win?.destroy(); app.quit() }
  }
}

app.whenReady().then(async () => {
  win = new BrowserWindow({
    width: 1240, height: 820, minWidth: 720, minHeight: 520, title: 'IWXA | Browser qualification',
    webPreferences: {
      preload: path.join(dist, 'preload.cjs'), nodeIntegration: false,
      contextIsolation: true, sandbox: true, webviewTag: true, devTools: false
    }
  })
  const owner = win
  const profile = session.fromPartition(partition)
  profile.setPermissionRequestHandler((_contents, _permission, callback) => callback(false))
  profile.setPermissionCheckHandler(() => false)
  owner.webContents.on('will-navigate', event => event.preventDefault())
  owner.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  owner.webContents.on('will-attach-webview', (event, preferences, params) => {
    if (guest || params.src !== 'about:blank' || params.partition !== partition) {
      event.preventDefault()
      return
    }
    delete preferences.preload
    Object.assign(preferences, {
      nodeIntegration: false, nodeIntegrationInSubFrames: false, contextIsolation: true,
      sandbox: true, webSecurity: true, allowRunningInsecureContent: false, webviewTag: false,
      devTools: false, partition
    })
  })
  owner.webContents.on('did-attach-webview', (_event, attached) => {
    if (guest || attached.hostWebContents !== owner.webContents || attached.session !== profile) {
      attached.close()
      return
    }
    guest = attached
    adapter = new VisibleBrowserAdapter({
      sessionId: 'qualification', guest: attached,
      isFocused: () => !owner.isDestroyed() && owner.isFocused() && !owner.isMinimized(),
      reveal: () => { if (!owner.isDestroyed()) owner.show() },
      cursor: state => { if (!owner.isDestroyed()) owner.webContents.send('iwxa:browser-cursor', state) },
      controlChanged: publishState,
      allowNavigation: allowedUrl,
      // Production authorization remains the existing Agent ToolRuntime. The harness is not it.
      authorize: (name) => name !== 'execute' && name !== 'call_web_tool'
    })
    attached.on('will-navigate', event => { if (!allowedUrl(event.url)) event.preventDefault() })
    attached.on('will-redirect', event => { if (event.isMainFrame && !allowedUrl(event.url)) event.preventDefault() })
    attached.setWindowOpenHandler(details => {
      if (details.postBody || !allowedUrl(details.url)) adapter?.notePopupBlocked()
      else void attached.loadURL(normalizeBrowserUrl(details.url)).catch(() => adapter?.notePopupBlocked())
      return { action: 'deny' }
    })
    attached.on('content-bounds-updated', event => event.preventDefault())
    attached.on('did-navigate', publishState)
    attached.on('did-navigate-in-page', publishState)
    attached.on('page-title-updated', publishState)
    attached.once('destroyed', () => { void shutdown().catch(() => app.quit()) })
    void adapter.setPresented(true).then(() => {
      publishState()
      if (mcpRequested && adapter && !stdio && !quitting) stdio = serveBrowserStdio(adapter)
    }).catch(() => { void shutdown().catch(() => app.quit()) })
  })
  owner.on('blur', () => adapter?.hideCursor())
  owner.on('close', event => {
    if (!quitting) { event.preventDefault(); void shutdown().catch(() => app.quit()) }
  })
  ipcMain.handle('iwxa:browser-enable', async (event, enabled: unknown) => {
    const binding = requireAdapter(event)
    if (typeof enabled !== 'boolean') throw new Error('invalid_control_state')
    await binding.adapter.setAgentEnabled(enabled)
    publishState()
    return binding.adapter.agentEnabled
  })
  ipcMain.handle('iwxa:browser-manual', async (event, op: unknown, url: unknown) => {
    const binding = requireAdapter(event)
    await binding.adapter.setAgentEnabled(false)
    publishState()
    const history = binding.guest.navigationHistory
    switch (op) {
      case 'open':
        if (typeof url !== 'string' || url.length > 8192) throw new Error('invalid_url')
        await navigate(url)
        break
      case 'back': if (history.canGoBack()) history.goBack(); break
      case 'forward': if (history.canGoForward()) history.goForward(); break
      case 'reload': binding.guest.reload(); break
      case 'stop': binding.guest.stop(); break
      default: throw new Error('unknown_manual_operation')
    }
  })
  ipcMain.handle('iwxa:browser-arrive', (event, arrival: BrowserCursorArrival) => {
    const binding = requireAdapter(event)
    if (!arrival || arrival.sessionId !== 'qualification' || arrival.tabId !== binding.adapter.tabId ||
        !Number.isSafeInteger(arrival.sequence) || typeof arrival.documentId !== 'string')
      throw new Error('invalid_cursor_arrival')
    binding.adapter.arrive(arrival)
  })
  owner.webContents.on('did-finish-load', publishState)
  await owner.loadFile(chromePath)
}).catch(() => { app.exit(1) })
