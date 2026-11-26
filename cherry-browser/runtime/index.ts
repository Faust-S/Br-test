// SPDX-License-Identifier: AGPL-3.0-only
export { VisibleBrowserAdapter, type VisibleBrowserHost } from './visible-browser'
export { createBrowserMcpServer, serveBrowserStdio } from './mcp-port'
// Reuse the upstream discovery schema; no second browser or Agent registry.
export { sessionToolDefinitions } from '@main/ai/mcp/browserToolDefinitions'
export type { BrowserAutomationResult } from '@main/features/browser/automation'
