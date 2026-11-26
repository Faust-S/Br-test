// SPDX-License-Identifier: AGPL-3.0-only
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { automationToolName } from '../runtime/automation-names.mjs'

test('every original automation operation maps to an actual original browser tool', async () => {
  const automation = await readFile(new URL('../vendor/cherry-studio/src/main/features/browser/automation.ts', import.meta.url), 'utf8')
  const catalog = await readFile(new URL('../vendor/cherry-studio/src/main/ai/mcp/browserTools.ts', import.meta.url), 'utf8')
  const schemaBlock = automation.split('const schemas = {')[1].split('export interface BrowserAutomationResult')[0]
  const original = [...schemaBlock.matchAll(/^  ([a-zA-Z]\w*): z\b/gm)].map(match=>match[1])
  assert.equal(original.length,20)
  for (const name of original) {
    const tool = automationToolName(name)
    assert.ok(tool, name)
    assert.ok(catalog.includes("'" + tool + "'"), tool)
  }
})
test('the high effect aliases use exactly the corresponding original MCP permission', () => {
  assert.equal(automationToolName('evaluate'),'execute')
  assert.equal(automationToolName('callWebTool'),'call_web_tool')
  for(const name of ['constructor','__proto__','toString','reset',null,1]) assert.equal(automationToolName(name),undefined)
})
