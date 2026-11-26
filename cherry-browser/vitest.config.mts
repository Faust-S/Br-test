// SPDX-License-Identifier: AGPL-3.0-only
import { defineConfig } from 'vitest/config'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
const root = path.dirname(fileURLToPath(import.meta.url))
const vendor = path.join(root, 'vendor/cherry-studio/src')
const names = ['actions','snapshot','refRecovery','GuestSession','WebMcpTools','screenshot','inspection','BrowserCursor']
export default defineConfig({
  resolve: { alias: [
    { find: '@logger', replacement: path.join(root, 'runtime/compat/logger.ts') },
    { find: '@main/core/lifecycle', replacement: path.join(root, 'runtime/compat/lifecycle.ts') },
    { find: '@main', replacement: path.join(vendor, 'main') },
    { find: '@shared', replacement: path.join(vendor, 'shared') }
  ] },
  test: {
    environment: 'node',
    include: [
      ...names.map(name => 'vendor/cherry-studio/src/main/features/browser/__tests__/' + name + '.test.ts'),
      'vendor/cherry-studio/src/renderer/components/__tests__/BrowserCursorAnimation.test.ts',
      'tests/*.test.ts'
    ],
    passWithNoTests: false
  }
})
