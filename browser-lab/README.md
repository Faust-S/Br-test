# Open Browser Lab — public Obscura desktop experiment

This is an independently authored **public**, vendor-neutral browser-prototype project.
It uses Tauri 2 (privileged chrome only), React 19, TypeScript, Rust and **Obscura v0.2.6**.
The websites run in native Obscura; Tauri's Windows WebView2 is used for the chrome,
not as a hidden fallback page engine.

## What is real
- Desktop top tabs, address/search input, back/forward, bookmarks, keyboard shortcuts.
- Native Rust controller starts a pinned Obscura binary, manages CDP targets, sends
  click, key, text, wheel and viewport-size updates, captures PNG from same engine.
- The browser chrome keeps its own bookmarks and history. The restart-engine command
  terminates only its **owned** Obscura child and reopens permitted tab URLs (Ctrl+Alt+Shift+E);
  runtime restart/recovery still requires installed-GUI acceptance.
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

The public GUI smoke uses a verified official Obscura v0.2.6 executable, a
local-only HTTP fixture, a temporary profile, an Xvfb desktop, and the **actual
Tauri window**. In Linux CI it checks page rendering, mouse/keyboard input,
two tabs, owned-process restart and network readback. It does **not** certify
an installed Windows app or a native compositor.

Only for local test fixtures, set `OBSCURA_LAB_FIXTURE=1` and explicitly grant
the particular `http://127.0.0.1:PORT` origin. Fixture mode now refuses all
non-127.0.0.1 grants and navigations, because the engine's private-network
flag is enabled for this test mode; it **must not be used for ordinary browsing**.
This guard does not constrain subresources, redirects, DNS rebinding or socket
access; a future OS and engine network sandbox must enforce those policies.

There is **no** arbitrary-page native command bridge. CDP is bound to
loopback and protected by a fresh 64-character random bearer token passed only
in the child process environment and native WebSocket Authorization header.
The original upstream CDP server also rejects browser-origin WebSockets.
Native GUI tests assert that an unauthenticated local connection receives 401.
This protects against opportunistic unauthorized CDP clients, **not** arbitrary
same-user code execution or OS-level inspection of process environments.
The native controller now starts Obscura with `--port 0 --ready-file`
and validates the reported PID and loopback address, removing its former
bind-close-rebind port race. A hash check alone does not establish
executable origin/signature authenticity. The OS process sandbox and
network policy enforcement remain unqualified.

## Limitations
- PNG frames are periodically sampled, not a native 60 FPS compositor.
- Accessibility/IME, downloads, hostile-site networking, DNS rebinding,
  redirects, file upload permission dialogs, app install/update and crash
  cleanup remain unqualified.
- The **unmodified** upstream Obscura 0.2.6 binary still does not preserve
  IndexedDB across restart. The open `engine-overrides/obscura-v0.2.6/`
  patch has passed public Windows/Linux process-restart tests, but this
  standalone GUI currently loads an explicitly adopted binary and does
  **not** automatically bundle that patch. No T2 Workspace results or
  receipts have been qualified; no second canonical database is created.
- No Gecko is bundled. No code from any private IWXA repository is included.
- A green workflow means source compilation and real *fixture* actions,
  **not** general-web, protected T2 or installed-application acceptance.

Developer branch: `work/2026-11-01-public-browser-core`.
