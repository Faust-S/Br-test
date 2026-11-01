# IWXA Browser — Obscura 0.2.6 versus Gecko, public integrated engine experiment
Date: 2026-11-01. Status: **PUBLIC_ENGINE_STUDY_PASS; T2_AND_CUSTOM_GUI_NOT_QUALIFIED**.

## Reproducibility

Run: https://github.com/Faust-S/Br-test/actions/runs/41292145637, both Windows Server 2022 and Ubuntu 24.04 jobs concluded success. Source: `comparison/architecture_v026.py`, branch `work/2026-11-01-browser-architecture-v026`. Obscura binary from official `v0.2.6` release, verified SHA256 asset digest before execution. Gecko/Firefox from hosted runner with a downloaded, pinned official geckodriver. Generic public fixture in `comparison/iwxa-ui-fixture.html`; no private T2 or IWXA product code.

**Be precise:** green Actions means that the public study completed and required generic page interactions passed. The **Obscura IndexedDB restart check remains FAIL**, a recorded *product blocker* not a test-infrastructure failure. None of this is a custom Tauri GUI benchmark.

## Actual same-machine, same public fixture results

| Metric | Windows Obscura 0.2.6 | Windows Firefox | Linux Obscura 0.2.6 | Linux Firefox |
|---|---:|---:|---:|---:|
| One tab sampled RSS sum, MiB | 46.1 | 913.6 | 62.1 | 1344.2 |
| Five tabs sampled RSS sum, MiB | 62.9 | 1023.8 | 75.5 | 1672.9 |
| Pointer click | PASS | PASS | PASS | PASS |
| Keyboard input | PASS | PASS | PASS | PASS |
| Wheel scroll | PASS | PASS | PASS | PASS |
| Desktop PNG | PASS | PASS | PASS | PASS |
| Responsive PNG | PASS | PASS | PASS | PASS |
| IndexedDB 3-store commit+abort | PASS | PASS | PASS | PASS |
| IndexedDB **after full browser process restart** | **FAIL** | **PASS** | **FAIL** | **PASS** |

For Obscura, persisted `localStorage` was `saved`, but the three IndexedDB entries returned `[null,null,null]` on both OSes. Firefox returned `[7,7,7]`. This matches the released v0.2.6 in-memory `IndexedDbStorage` code.

## On-demand Gecko cost **while an Obscura session stays alive**

Measured separately on the same runner on a single generic page, 8 sequential RSS snapshots about 150ms apart. **Median of sampled combined process RSS**, not warm steady-state of the whole product.

| Condition | Windows, MiB | Linux, MiB |
|---|---:|---:|
| Obscura open, Gecko not running | **26.71** | **46.84** |
| Both Obscura and Firefox open | **817.02** | **1335.96** |
| Firefox closed, Obscura same JS session remains | **26.66** | **46.84** |

The test also verified that the original Obscura JS marker survived launching and stopping Gecko; Firefox did not see Obscura's localStorage value, and its changes did not change Obscura's value. This **demonstrates independent browser profiles and sessions**, not a seamless fallback of the same React form, cookies, IndexedDB or T2 Workspace.

The two tables measure **different moments** of the test and cannot be conflated: 46.1 MiB is a sampled peak from the full paired Obscura page flow, while 26.71 MiB is a later short-window median with one page. They are not contradictory, but also not a product memory forecast.

## Interpretation

1. If an optional Gecko runtime is completely stopped, extra steady-state engine process memory can approach zero. Its **on-demand launch** still costs CPU/time, and two processes working simultaneously cost substantially more.
2. Product option B (Obscura+Gecko) increases compatibility potential but adds second-origin profiles, side-effect replay hazards, additional security/update surface, and difficult Windows Gecko embedding. The test uses *headless Firefox*, **not the same native IWXA browser chrome with embedded Gecko**.
3. Option A (Obscura-only) keeps one web engine and simplifies browser/Workspace authority, but is blocked on durable IndexedDB, full visual compatibility and sandbox/site isolation for hostile pages.
4. No valid p95 from one paired run; summing process RSS may double-count shared pages; no full GPU, Tauri/WebView2/Qt UI, Windows Job Objects, actual multimedia, crash/sleep or Android tests. Do not claim generic internet compatibility or absolute independence.
5. Decision remains: Obscura-only as initial required engine; Gecko as optional external compatibility reference pending an actual quantified benefit from *embedded* dual-engine architecture. **User-facing browser is not released**.

## Next tests

A. Installed Windows/Linux Tauri/React custom browser chrome + native Obscura viewport with real mouse/IME/keyboard/scroll/downloads, whole-process memory and GPU/latency.
B. Actual T2 Host/Kernel canonical Result write, receipt, readback, revision, browser restart, import/export `.iwxa`; must **not** be replaced with dummy stores or plain JSON files.
C. Hostile-site/OS sandbox tests with native authenticated IPC, process ownership, cross-origin/SSRF/local-file/CSP controls, denial of untrusted native commands.
D. At least one complete Learn page route, one ArtBazi calculator with asserted values, and external modern web samples. A visible H1 is insufficient.
