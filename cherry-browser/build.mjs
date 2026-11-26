// SPDX-License-Identifier: AGPL-3.0-only
import { build } from 'esbuild'
import { cp, mkdir, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const root = path.dirname(fileURLToPath(import.meta.url))
const vendor = path.join(root, 'vendor/cherry-studio/src')
const alias = {
  '@logger': path.join(root, 'runtime/compat/logger.ts'),
  '@main/core/lifecycle': path.join(root, 'runtime/compat/lifecycle.ts'),
  '@main': path.join(vendor, 'main'),
  '@shared': path.join(vendor, 'shared')
}
const common = { absWorkingDir: root, bundle: true, logLevel: 'warning', metafile: true }
const native = await build({
  ...common, entryPoints: ['harness/main.ts'], outfile: 'dist/main.mjs',
  platform: 'node', format: 'esm', target: 'node22', packages: 'external', alias, external: ['electron']
})
const library = await build({
  ...common, entryPoints: ['runtime/index.ts'], outfile: 'dist/browser-module.mjs',
  platform: 'node', format: 'esm', target: 'node22', packages: 'external', alias, external: ['electron']
})
const qualification = await build({
  ...common, entryPoints: ['qualification/native-main.ts'], outfile: 'dist/qualification-main.mjs',
  platform: 'node', format: 'esm', target: 'node22', packages: 'external', alias, external: ['electron']
})
const preload = await build({
  ...common, entryPoints: ['harness/preload.ts'], outfile: 'dist/preload.cjs',
  platform: 'node', format: 'cjs', target: 'node22', external: ['electron']
})
const renderer = await build({
  ...common, entryPoints: ['harness/renderer.ts'], outfile: 'dist/renderer.js',
  platform: 'browser', format: 'iife', target: 'chrome131', alias
})
// This is a compile-time check against accidental inclusion of the Cherry application.
const paths = [...Object.keys(native.metafile.inputs), ...Object.keys(library.metafile.inputs), ...Object.keys(qualification.metafile.inputs), ...Object.keys(preload.metafile.inputs), ...Object.keys(renderer.metafile.inputs)]
const forbidden = paths.filter(p => /(?:@application|@data\/|BrowserSessionService\.ts$|BrowserGuestRegistry\.ts$|AgentBrowserRegistry\.ts$|mcp\/controller\.ts$|mcp\/server\.ts$|src\/main\/data\/|src\/main\/services\/)/.test(p))
if (forbidden.length) throw new Error('Unexpected Cherry platform dependency: ' + forbidden.join(', '))
const external = [...Object.values(native.metafile.outputs), ...Object.values(library.metafile.outputs), ...Object.values(qualification.metafile.outputs)].flatMap(output => output.imports).filter(item => item.external)
if (external.some(item => /^@(main|shared|logger|application|data|renderer)(\/|$)/.test(item.path)))
  throw new Error('Unresolved Cherry platform alias')
await mkdir(path.join(root, 'dist'), { recursive: true })
await cp(path.join(root, 'harness/index.html'), path.join(root, 'dist/index.html'))
await cp(path.join(root, 'harness/style.css'), path.join(root, 'dist/style.css'))
await cp(path.join(vendor, 'renderer/assets/images/browser-cursor.svg'), path.join(root, 'dist/browser-cursor.svg'))
await writeFile(path.join(root, 'dist/build-inputs.json'), JSON.stringify([...new Set(paths)].sort(), null, 2))
console.log('BROWSER_BUILD_INPUTS', new Set(paths).size)
