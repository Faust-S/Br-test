# IWXA Universal Program Integration — Rust/Tauri host integration (04.11.2026)

## Decision

The reusable native Rust CLI port is `universal-install/host-bindings/rust-port` (`iwxa-program-native-port`, version 0.1.0). Its only direct third-party dependency is `serde_json = "1"`, already used by Atlas and Center. The public JS/TS WebView binding is `host-bindings/tauri-adapter.mjs`; it imports no Node APIs. The existing `host-port.mjs` remains the versioned envelope entry point.

No permanent daemon, Python runtime or backend service is required. The product host owns application-level decisions, install/remove approvals, credential providers, Task/Result, persistent receipts, lifetime and serialisation of stateful port calls. Do not copy Product Core or change any product 0.2/0.1.

## Native integration recipe (future product branch only)

1. Add a local path dependency to the existing Tauri application's `src-tauri/Cargo.toml`:

   `iwxa-program-native-port = { path = "<approved-relative-path>/rust-port" }`

2. In the product's existing Tauri host, keep one `CliPort` instance in its existing app state (e.g. `Mutex<CliPort>`), selected with the manifest and executable path by the native host, **not arbitrary frontend data**.
3. Expose a small native Tauri command `iwxa_program_connection` accepting `serde_json::Value` envelope and returning `serde_json::Value` through `port.dispatch(&envelope)`. The host still decides when to create/replace the port; `dispatch` does not install anything.
4. In WebView, reuse the host's existing Tauri invocation function:

```js
import {invoke} from "@tauri-apps/api/core";
import {createTauriProgramPort} from "./host-bindings/tauri-adapter.mjs";
const port = createTauriProgramPort(invoke);
const info = await port.inspect({});
// Connected is not installed; actual execution is a separate call.
```

5. Native host returns stable contract observations (`IWXA_PROGRAM_CONNECTION/1.0.0`) and does not write receipts, grant privileges or run background polling. Disconnect only drops the selected connection; removing the installed program is explicitly separate.

This recipe is **not** integrated into the actual Atlas, Center or Agent products. Their released branches remain unchanged.

## Technical verification

- Rust library unit tests validate version boundaries, manifest-driven arguments and input mismatch.
- The native qualification program uses the real jq/yq binary from the existing pinned `ManagedArtifactBackend` workflow, observes version, executes two parameterized calculations via the JSON envelope, verifies capabilities, disconnects, reconnects and checks unsupported/wrong-version operations.
- The JS Tauri wrapper is tested via an injected native-like host callback that actually runs the CLI. This is a contract/transport proof, not a native Tauri GUI PASS.
- The same CI job continues running generic installer regression, HTTP, local Git, public GitHub, and I3 Win32 linked-window fixtures.

## Limitations / acceptance gates

- The Rust crate needs a real compiled cross-platform qualification before claiming Windows/macOS support; a Rust unit-test PASS alone is insufficient.
- A local `cargo build` can create an executable in `target/debug`; this is a **test tool**, not a mandatory shipped binary. The library compiled into a Tauri host adds no standalone process.
- Cold process launch of the Rust test binary must not be confused with in-process library invocation; release footprint and loaded-memory delta remain to be measured in actual product builds.
- Product-specific platform permissions, code signatures, installed GUI navigation, and active user credentials remain the product host's responsibility.
- An existing connected program can lose its executable, version or interface; the host must surface `CONNECTION_LOST` and reverify before trusting stale readiness.
- No UI embedding I4/I5, arbitrary EXE control, automatic OAuth private-source qualification, new Store/IAM or installation orchestration is claimed.

Integration order: publish and qualify this public component; open independent product integration work for Agent 0.3, Center and Atlas after owner approval; keep release branches and other developers' work untouched.
