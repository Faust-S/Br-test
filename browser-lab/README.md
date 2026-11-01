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
   - `OBSCURA_LAB_ALLOWED_ORIGINS`: optional **debug-only** comma-separated HTTPS origins. A normal installed browser starts without this variable; users choose Learn or ArtBazi on the new-tab page.
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
- The **unmodified** upstream Obscura 0.2.6 binary does not preserve
  IndexedDB across restart. A resource-bundled patched-engine build has
  independently passed Windows/Linux native Tauri full-restart IndexedDB
  readback. A normal unpatched dev build is **not** equivalent to that candidate.
  No T2 Workspace results or receipts have been qualified; no second canonical
  database is created.
- No Gecko is bundled. No code from any private IWXA repository is included.
- A green workflow means source compilation and real *fixture* actions,
  **not** general-web, protected T2 or installed-application acceptance.

Developer branch: `work/2026-11-01-public-browser-core`.

## Browser chrome design (public preview)

The chrome is now labelled **IWXA Browser Preview** (public code, no private IWXA components): familiar horizontal tabs, unobtrusive address bar, start page with the same search input, lightweight history and bookmarks, and a technical restart action behind the options menu. The browser retains its explicit origin restrictions; the UI must not claim that general Internet browsing is sandboxed or approved. Real Linux/Windows GUI screenshots are captured by the existing workflows.

## Compact tab strip (2026-11-01)

The permanent IWXA logo and preview badge were removed from the tab strip. The OS window icon/title and the new-tab page retain identity. The strip is ~41 CSS pixels tall and starts tabs at the left edge; the new-tab button follows the tab rail. Multiple tabs scroll horizontally, with the selected tab scrolled into view. The Home control now returns the current tab to the browser start page rather than duplicating the new-tab action. Native fixture UI checks only accept page-colored targets inside the Obscura content area, not similarly colored UI logos.

## Event-driven Obscura frames (native integration)

The browser now uses Obscura's upstream `Page.startScreencast` and activity-driven `Page.screencastFrame` events rather than calling `Page.captureScreenshot` every 750 ms. A short native CDP read on a 125-ms UI tick coalesces frames and acknowledges consumption with `Page.screencastFrameAck`; no new render process or IPC authority is introduced. Only one selected tab streams at a time. Hidden windows stop read ticks, and Obscura's in-flight cap handles backpressure. The original browser engine still draws and encodes PNG, so this is not yet zero-copy GPU compositing. Real GUI/Windows acceptance is required; the public fixture alone is insufficient.


## One-engine patched installation candidate

The first **resource-bundled patched-engine** route now lives in the public
`browser-lab/scripts/stage_patched_engine.py` and
`src-tauri/tauri.patched.conf.json`. It is not a final signed release.

1. Check out upstream Obscura at exactly
   `8534b401898ed17b62f69bdc3d5eb186bffb0d60`.
2. Apply the two existing public source overrides from `engine-overrides/`.
3. Build `obscura-cli` with `--features render --bin obscura`.
4. Set `OBSCURA_PATCHED_BINARY` to the resulting executable and run
   `python browser-lab/scripts/stage_patched_engine.py`.
5. Build from `browser-lab/` with
   `npx tauri build --debug --no-bundle --config src-tauri/tauri.patched.conf.json`.

The build script validates the staged executable and embeds its exact SHA-256
in the native application; runtime validates the packaged Obscura again before
executing it. A DEBUG-only override may use an adopted
`OBSCURA_LAB_BINARY` plus `OBSCURA_LAB_SHA256`, while a release build cannot
select its engine from those environment variables. Packaging a binary does
**not** make it safe to run untrusted Internet pages and does not certify
cross-platform installation, automatic updates, executable signing or
isolation from the operating system.

The dedicated public patched-bundle workflow executes an independent,
real Tauri GUI IndexedDB write and readback after terminating the complete
owned process tree and relaunching the application using the **same** engine
profile. This is a browser session qualification, not acceptance of T2
Workspace receipts or Exchange.

## Browser tab restoration (public prototype)

The native controller now persists only the **URLs and selected-tab index**
in the local Obscura profile as \`browser-tabs-v1.json\`. Writes use a
same-directory temporary file, file synchronization and atomic replacement.
This is not a Result store, a second IndexedDB, or a duplicate Workspace
history. No page contents, receipts or credentials are serialized into this
browser metadata file (URLs can still contain sensitive query parameters).

On cold launch, the controller reads the bounded file and **revalidates every
URL against current origin grants**. Disallowed, corrupt or stale entries never
gain an engine tab. A new default tab is used if no entries are permitted.
After the application is closed and restarted, the active tab and its URL
reappear while the engine's original IndexedDB profile remains intact.

The public packaged-GUI scenario now checks that the second Tauri launch
displays the previously open IndexedDB page *without manually navigating
again*, then performs a real click and reads the same committed stores.

## No-environment product entry (2026-11-01)

The regular browser no longer requires `OBSCURA_LAB_ALLOWED_ORIGINS`.
On a fresh profile it shows two explicit user choices:
`https://learn.iwxa.ru` (Learn) and `https://artbazi.ru` (ArtBazi).
Choosing either site approves **that exact HTTPS origin only** and opens it
in the current tab. No wildcard, arbitrary origin, search provider, or
automatic grant has been introduced.

The existing atomic `browser-tabs-v1.json` gains an optional
`granted_sites` field containing only compiled-in site IDs. Old sessions
without that field load safely. Those IDs are revalidated on startup and
permit the browser to restore its last tabs without environment configuration.
The options menu lets the user forget site approvals and close tabs.
Debug-only fixture grants remain separate from persisted site approvals.

This is a restricted preview, not a secure arbitrary-Internet browser.
Subresource requests, redirects, DNS rebinding, OS process isolation,
downloads, and full T2 Workspace routes remain outside acceptance.

## Typed-address consent flow

Typing a Learn or ArtBazi deep link in the address bar on a fresh profile
opens an explicit, keyboard-accessible approval dialog instead of producing an
opaque "not granted" error. Approving passes the full requested URL to the
existing native OpenSite command; Rust validates that URL against the selected
compiled-in exact HTTPS origin **before** granting it. Cancel leaves site
permissions unchanged. Addresses on other sites do not prompt or navigate.
Debug fixture runs retain their existing explicit-origin test behavior.

For an isolated normal-mode test, DEBUG builds may override the native
profile with an absolute `OBSCURA_LAB_PROFILE_DIR` without enabling fixture
network mode. Release builds ignore that profile environment override.

## Pending external HTTPS navigation

An HTTPS page may still be loading when Page.navigate acknowledges. The native
controller preserves the requested URL instead of replacing it with a stale
about:blank response. The tab shows Loading page... and a separate page-loading
surface until a real Obscura frame arrives. On subsequent metadata refreshes
the actual document URL, title and readiness replace the pending state.
The stale-document grace is bounded at 25 seconds. This is not a network
sandbox and it does not qualify an external website as a T2 Result.

## Real public-site desktop probe

The existing patched-engine bundled-GUI workflow now reuses the **same built
Linux binary** for an additional real-user Learn/ArtBazi route. It starts
the browser without origin-grant environment variables and with a unique
DEBUG profile, types each public domain, approves via the new dialog, opens
both sites, terminates the app, and checks that both tabs and approvals restore.
Then it visits each published /workspace route and captures desktop evidence.
No separate build or workflow is introduced; the live network probe is
explicitly reported as PASS or HOLD without blocking the proven IndexedDB gate.

This probe qualifies only user navigation, opt-in, and browser-owned session
restoration. It deliberately does **not** claim page hydration, T2 Result
creation, persistence of a real Workspace object, or hostile-site sandboxing.

## Native GUI loading synchronization

The Linux patched-GUI artifact from commit 1d5ebb6 showed a real fixture
button and then an intermittent loading surface; the resulting click did not
produce an HTTP IndexedDB write event. This does **not** establish an
IndexedDB data-loss problem. The GUI now accepts a target only after three
stable page screenshots, saves the verified screenshot itself, and still
requires the original real HTTP event and committed [7,7,7] values.
The native controller refreshes the tab metadata on the first frame after
pending navigation, avoiding a stale loading surface until the next 2-second
periodic refresh without increasing idle polling. The public live-sites probe
now collects independent evidence even when the separate IDB gate fails,
using the same compiled Obscura binary and existing workflow.

## Corrected public-site GUI entry synchronization

The live-site test initially typed its first URL while the frontend still
displayed `Engine not started`: Tauri window creation and the light toolbar
are **not** proof that the native Obscura engine finished `Boot`. The test
then sent `Tab`/`Enter` without an open consent dialog, inadvertently
creating another empty tab. The screenshots and browser-owned session file
showed two blank tabs with no approved origins, not a remote-site failure.

The revised single live-site probe waits for a **new native session write**
and visible Learn/ArtBazi site cards before interacting. Learn uses a real
card click; ArtBazi uses the typed URL and validates a visible consent modal
before choosing Allow. The probe keeps original tab and consent readback,
cold restart, and Workspace-address navigation in the **same bundled engine
build**. It captures screenshots of the remote pages for independent visual
review. It does **not** upgrade URL persistence into proof of page hydration
or T2 Result persistence.

## Real Learn render confirmed and compact startup hashing

GitHub's `effbac1` Linux artifact contains the actual bundled Obscura page
with the Learn product rendered, not merely a saved address. The test next
missed the tab `+` button: the live screenshot puts it around x=253 after
one tab, whereas the old action clicked x=285 outside the hit region.
The existing live-site test now clicks the real button correctly and requires
a green native document-ready indicator after opening each public domain and
each /workspace route. Document-ready is not proof of hydrated React,
real T2 Result persistence or receipt readback; those still require
independent product evidence.

At browser start, packaged-engine SHA-256 verification previously loaded
the entire Obscura executable into the shell's heap with `fs::read`.
The controller now hashes in 64 KiB chunks, retaining byte-for-byte digest
validation and reducing peak transient memory on both Windows and Linux.
This does not modify the engine binary, database or process topology.

## Confirmed two real sites and X11 pointer recovery

The bundled Linux run for 11e5241 confirmed both Learn and ArtBazi actually
loaded, with visible green document-ready status and user-approved origins.
After a complete Tauri restart, both tabs and approvals were read back
successfully. The subsequent Workspace navigation stalled in an X11 test
helper: `xdotool mousemove --sync` timed out on a busy restored page.
The native GUI probe now uses non-blocking X11 pointer movement followed by
the same real mouse click. It still requires session persistence, exact
approved origins and real document-ready evidence. T2 Result writes and
readback remain unqualified.

## Workspace form keyboard defaults

Upstream Obscura v0.2.6 emits keydown/keyup but omits browser-native
Tab/Shift+Tab focus traversal and Ctrl+A selection defaults. The browser's
existing native controller now applies those limited actions to the **active
site document** on the user's explicit key press. Tab navigation respects
visible and enabled focusable controls and positive tabindex order. Ctrl+A
selects the contents of an editable input, textarea or contenteditable only.
Browser-owned shortcuts (Ctrl+L/T/W/R, Alt+Left/Right and engine restart)
remain reserved; all other modifier keys reach the website rather than
being silently dropped. This does not claim full OS clipboard, IME, shadow-DOM
focus order or accessibility qualification.

The already existing Windows and Linux native GUI smoke performs ordinary
typing in a real Obscura input, then selects and replaces the text using
Ctrl+A, and requires the real HTTP readback for the resulting value. No
second renderer or additional workflow is introduced.

## Real T2 bootstrap diagnosis: no false document-ready claims

Screenshots from the accepted real-site navigation revealed that Learn's
Workspace was still displaying `Loading domain configuration` and ArtBazi's
Workspace contained only site chrome and an empty content region. Thus
`document.readyState=complete` proved HTML load, **not** operational T2
Workspace hydration. The source T2 Host fetches three exact same-origin
configuration documents in parallel, before mounting the Workspace.

To identify the actual compatibility failure without duplicating T2, a
**DEBUG-only** keyboard action (Ctrl+Alt+Shift+D) runs a fixed read-only
site-local probe. It reports HTTP codes, JSON validity, response lengths
and basic DOM-size counts for those three required files to native stderr.
It never logs fetched configuration contents, page text, user data or
credentials. It is absent from release builds and forbidden in fixture mode.
No additional user-menu item, background process, database, Host or Kernel
was added.

The existing bundled Linux GUI workflow invokes the probe after loading
both real Workspace routes. The artifact can now distinguish missing
configuration responses, script readiness and a merely loaded HTML page.
Actual T2 Results and receipt readback remain HOLD until witnessed.

## T2 Host mount is distinct from HTML load

In the public packaged-browser route (027a30e), Learn and ArtBazi each returned
HTTP 200 and valid JSON from all **three** normative Host configuration paths.
The early screenshot of Learn still displayed the bootstrap placeholder; the
ArtBazi screenshot showed site-owned chrome but no workspace content. No
config request was missing. The canonical T2 Host has the explicit
`data-t2-ui="workspace"` root marker in its source.

The normal browser now marks an approved `/workspace` URL **ready** only
after that canonical marker exists in the actual Obscura document. A loaded
HTML page without mounted Host stays in an amber `Workspace initializing`
state rather than falsely displaying `Page ready`. The same already-present
debug-only probe records a boolean Host-mount result and captures the
post-probe window; it is not a release diagnostic or user menu action.
The real-site route retains separate indicators for HTML navigation, Host
mount and T2 Result readback, never claiming a Result on URL persistence.

## Live ArtBazi user operation, not merely document readiness

The 76270bc Windows/Linux native and bundled checks passed. In real Linux
screenshots the ArtBazi T2 Overview was rendered, including the actionable
"Новая рабочая область" button, while the one-element Host CSS selector
incorrectly reported "not mounted". Learn's navigation also appeared but
multiple lazy cards were still loading. The status now checks the canonical
Host attribute and the normative `overview-view` marker **independently**,
using actual DOM attributes, so a partial Host and a visible Overview are
not conflated. Merely loading the HTML document is still not "ready".

The existing one-pass real-site GUI now attempts the first genuine
user-level T2 operation: the visible ArtBazi "Новая рабочая область" action,
followed by its real confirmation dialog. The existing DEBUG-only, read-only
probe returns only the bounding-box center for the two fixed T2 user
controls and whether the dialog/Overview is present. The test then uses
actual native mouse clicks. No T2 API call, Kernel fabrication, injected
state, or separate Workspace database is introduced. A successful click
is **not** a Result receipt and is not qualified as persistence until T2
readback after a full restart is independently demonstrated.

## Full T2 Workspace record readback after actual browser restart

The existing native Linux public-site scenario now requires more than opening
the ArtBazi creation dialog. It clicks the T2-owned confirmation button,
witnesses the real `#/workspace/:workspaceId` fragment generated by T2
Kernel, observes the mounted `workspace-view` component rather than an error,
then fully terminates the browser. A third launch with the same native
profile must restore the **same** generated route and display the same
record view after the user selects its tab. The report contains a SHA-256
fingerprint of the test-only route rather than its opaque ID. No T2 API
or database is called directly and the native browser has no new authority.

This acceptance qualifies only a T2 Workspace record once it passes.
A Workspace record is not a saved domain Result, revision receipt, readback
of a CRM operation, Exchange, or system isolation. Those remain HOLD until
their respective user operations have also passed.

## Bounded real hydration wait and one-time mutation

A live ArtBazi screenshot from 5998591 showed the site-owned header and
footer but **no** mounted Overview; the previous successful UI render was
not consistently reproducible. The native route now waits up to 70 seconds
for the actual `overview-view` and its user-facing create action, without
clicking blank space or inferring readiness from the browser address.

The readback launch only reopens the T2-created Workspace: it **never**
replays the creation operation. It checks the exact previously generated
fragment URL in the native saved tab session, then requires the real T2
`workspace-view` and absence of record errors. If hydration is unavailable
the run reports HOLD with screenshots rather than a false PASS. No domain
Result or revision receipt is claimed.

## Bounded module-script transport recovery

Public Obscura issue #1222 identifies transient module-script loading
failures that can strand Vite/React SPAs on their shell. Its proposed
PR #1226 was still unmerged and out of date against pinned 0.2.6. The
public engine overlay now uses a conservative three-attempt transport retry
with short backoff in Obscura's **existing** JS module loader. There are
no extra browser processes, requests on the normal successful path,
new databases, custom React fallback or second network client.

The existing packaged Windows/Linux workflow builds and tests the same
native engine with IndexedDB persistence, then attempts the real ArtBazi
creation/readback route. The retry is **not** considered proof of T2
compatibility until the actual React Overview mounts and a workspace record
survives full restart.
