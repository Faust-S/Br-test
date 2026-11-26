// SPDX-License-Identifier: AGPL-3.0-only
// Native qualification only, never shipped as the production browser host.
import { app, BrowserWindow, WebContentsView } from 'electron'
import { VisibleBrowserAdapter, serveBrowserStdio } from '../runtime/index'

const arg = process.argv.find(value=>value.startsWith('--fixture-origin='))
const origin = arg?.slice('--fixture-origin='.length)
if (!origin || !/^http:\/\/127\.0\.0\.1:[1-9][0-9]{0,4}$/.test(origin)) throw new Error('fixture_origin_required')

await app.whenReady()
const win = new BrowserWindow({ width: 1150, height: 760, show: true, title: 'IWXA Chromium native qualification' })
const view = new WebContentsView({
  webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false, devTools: false,
    partition: 'iwxa-browser-native-qualification' }
})
win.contentView.addChildView(view)
function place() {
  const [width,height] = win.getContentSize()
  view.setBounds({ x: 280, y: 0, width: Math.max(1,width-280), height })
}
place();win.on('resize',place)
await win.loadURL('data:text/html,<h1>IWXA browser qualification</h1><p>Actual Chromium guest on the right.</p>')
await view.webContents.loadURL('about:blank')
win.show();win.focus();view.webContents.focus()
const browser = new VisibleBrowserAdapter({
  sessionId:'native-qualification', guest:view.webContents,
  isFocused:()=>win.isFocused()&&!win.isMinimized(),
  reveal:()=>win.show(), cursor:()=>{},
  allowNavigation:url=>new URL(url).origin===origin,
  authorize:name=>!['execute','call_web_tool'].includes(name)
})
await browser.setPresented(true)
// The trusted qualification fixture establishes the grant, not an MCP command or a website.
await browser.setAgentEnabled(true)
const handle=serveBrowserStdio(browser)
let closing=false
async function shutdown(){
  if(closing)return;closing=true
  try {await handle.close()} finally {
    try {await browser.dispose()} finally {
      if(!view.webContents.isDestroyed())view.webContents.close()
      if(!win.isDestroyed())win.destroy()
      app.quit()
    }
  }
}
process.stdin.once('end',()=>{void shutdown().catch(()=>app.exit(1))})
win.on('close',event=>{if(!closing){event.preventDefault();void shutdown().catch(()=>app.exit(1))}})
