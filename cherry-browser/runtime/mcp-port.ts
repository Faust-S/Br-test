// SPDX-License-Identifier: AGPL-3.0-only
// The upstream schemas and handlers are the only browser tool catalog.
import { McpServer } from '@modelcontextprotocol/server'
import { serveStdio } from '@modelcontextprotocol/server/stdio'
import { sessionToolDefinitions } from '@main/ai/mcp/browserToolDefinitions'
import type { VisibleBrowserAdapter } from './visible-browser'

export function createBrowserMcpServer(adapter: VisibleBrowserAdapter): McpServer {
  const server = new McpServer({ name: 'iwxa-visible-browser', version: '0.6.7' })
  for (const { name, description, inputSchema } of sessionToolDefinitions) {
    server.registerTool(name, { description, inputSchema }, (args, context) =>
      adapter.callTool(name, args, context.mcpReq.signal))
  }
  return server
}

// Transport belongs to the host's original MCP connection. Closing it revokes AI only.
export function serveBrowserStdio(adapter: VisibleBrowserAdapter) {
  return serveStdio(() => {
    const server = createBrowserMcpServer(adapter)
    server.server.onclose = () => { void adapter.setAgentEnabled(false).catch(() => {}) }
    return server
  })
}
