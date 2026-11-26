// SPDX-License-Identifier: AGPL-3.0-only
// Only maps the original automation API spellings to the original MCP authority names.
const names = Object.freeze({
  getTab: 'snapshot', getAXState: 'snapshot', getScreenshot: 'screenshot',
  goto: 'open', evaluate: 'execute', goBack: 'go_back', goForward: 'go_forward',
  listWebTools: 'list_web_tools', callWebTool: 'call_web_tool',
  getConsoleMessages: 'console_messages', getNetworkRequests: 'network_requests',
  waitFor: 'wait_for', click: 'click', hover: 'hover', type: 'type', pressKey: 'press_key',
  scroll: 'scroll', selectOption: 'select_option', find: 'find', handleDialog: 'handle_dialog'
})
export function automationToolName(name) {
  return typeof name === 'string' && Object.hasOwn(names, name) ? names[name] : undefined
}
