# Open Browser Lab — public Obscura desktop experiment

This is an independently authored **public**, vendor-neutral browser-prototype project.
It uses Tauri 2 (privileged chrome only), React 19, TypeScript, Rust and **Obscura v0.2.6**.
The websites run in native Obscura; Tauri's Windows WebView2 is used for the chrome,
not as a hidden fallback page engine.

## What is real
- Desktop top tabs, address/search input, back/forward, bookmarks, keyboard shortcuts.
- Native Rust controller starts a pinned Obscura binary, manages CDP targets, sends
  click, key, text, wheel and viewport-size updates, captures PNG from same engine.
- A publicly reproducible Windows/Linux workflow compiles the desktop source and
  executes real native Obscura against a generic local fixture.
- The public fixture exercises mouse, keyboard, wheel, new tab, history and PNG.
  It does not use proprietary websites, private account tokens, or T2 contracts.

## Local development
1. Install Node 22+, Rust stable, Tauri 2 OS prerequisites.
2. Acquire render-enabled Obscura official v0.2.6 binary and **verify its SHA-256**.
3. Set the environment:
   - `OBSCURA_LAB_BINARY`: absolute native Obscura executable path
   - `OBSCURA_LAB_SHA256`: full verified executable SHA-256
   - `OBSCURA_LAB_ALLOWED_ORIGINS`: explicit comma-separated HTTPS origins
4. `cd browser-lab && npm install && npm run tauri:dev`.

Only for local test fixtures, set `OBSCURA_LAB_FIXTURE=1` and explicitly grant
the particular `http://127.0.0.1:PORT` origin. The engine's private-network
flag is enabled in this mode and **must not be used for ordinary browsing**.

There is **no** arbitrary-page native command bridge. CDP is loopback-only but
not authenticated against other local processes. It has a port-allocation race.
A hash check alone does not establish executable origin/signature authenticity.
The process supervisor and sandbox are not qualified.

## Limitations
- PNG frames are periodically sampled, not a native 60 FPS compositor.
- Accessibility/IME, downloads, hostile-site networking, DNS rebinding,
  redirects, file upload permission dialogs, app install/update and crash
  cleanup remain unqualified.
- Obscura upstream IndexedDB still does not survive process restart. This
  program **does not** claim persistent T2 results or invent a second database.
- No Gecko is bundled. No code from any private IWXA repository is included.
- A green workflow means source compilation and real *fixture* actions,
  **not** general-web, protected T2 or installed-application acceptance.

Developer branch: `work/2026-11-01-public-browser-core`.
