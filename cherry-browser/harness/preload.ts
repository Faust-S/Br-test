// SPDX-License-Identifier: AGPL-3.0-only
import { contextBridge, ipcRenderer } from 'electron'
contextBridge.exposeInMainWorld('iwxaBrowser', {
  enable: (enabled: boolean) => ipcRenderer.invoke('iwxa:browser-enable', enabled),
  manual: (operation: string, url?: string) => ipcRenderer.invoke('iwxa:browser-manual', operation, url),
  arrive: (arrival: unknown) => ipcRenderer.invoke('iwxa:browser-arrive', arrival),
  onState: (callback: (value: unknown) => void) => {
    ipcRenderer.on('iwxa:browser-state', (_event, value) => callback(value))
  },
  onCursor: (callback: (value: unknown) => void) => {
    ipcRenderer.on('iwxa:browser-cursor', (_event, value) => callback(value))
  }
})
