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


## Завершённая нативная матрица Rust и регрессия подмены файла

Исходный кандидат: `e2f61e0a7e000fe8e9db178d2dedc99631540136`, TREE `87feb14670941dc79d6cd5563afa8dc4d2e41535`. [GitHub Actions 41830997789](https://github.com/Faust-S/Br-test/actions/runs/41830997789) — **6/6 success**, все задания исполнялись на данном исходном SHA.

- Linux x64/ARM64: реальное выполнение jq 1.8.1 и yq 4.54.1 через Rust host PASS.
- Windows x64: jq и yq через Rust host PASS.
- Windows ARM64: yq через Rust host PASS. jq 1.8.1 — `UPSTREAM_UNSUPPORTED`, не PASS.
- macOS Intel/ARM64: jq и yq через Rust host PASS.
- Обе реализации CLI-коннектора Python/JS теперь сбрасывают состояние при изменении идентичности исполняемого файла. Реальные тесты с отдельной копией бинарника меняют метаданные файла и требуют `CONNECTION_LOST`. После `recover` старая отметка `capabilities[].verified` не сохраняется.
- Rust native port также проверяет изменение бинарника до вызова; регрессия с изменённой временной копией прошла на всех платформах. Фикстура допускает запись только в собственную временную копию: поставляемые бинарники могут не иметь прав на запись.
- Распознавание версии в Python/JS теперь учитывает границы токена с обеих сторон, а не принимает близкие строки.

Общая исходная ветка универсального установщика, Agent 0.2, Center 0.1 и выпущенный Atlas 0.2 не изменены. Эти результаты **не** заменяют установленную Tauri GUI-приёмку и замеры overhead внутри реальных продуктов. Размеры отдельных debug test binary зависят от платформы и не означают накладные расходы встроенной Rust-библиотеки.

Документальная фиксация не меняет проверенные исходники и не требует нового запуска CI.


## M8 — квалифицированный Tauri v2 command / IPC (04.11.2026)

Исходный кандидат `0966c235b17817e13c2d5d5639d72fa0b8f85fa7`, [GitHub Actions 41834717969](https://github.com/Faust-S/Br-test/actions/runs/41834717969): **7/7 PASS** — шесть стандартных OS/architecture заданий и отдельное `tauri-native-ipc` на Windows x64. Этот кандидат включает исправления идентичности CLI и сброса capability после потери HTTP-авторизации; ранее квалифицированные M1–M7 сценарии остаются PASS.

**Реальный код**: `host-bindings/rust-port/src/tauri_host.rs` — опциональная Tauri v2 команда `iwxa_program_connection`, основанная на том же `CliPort::dispatch` и host-owned `Mutex<CliPort>`. Обычная Rust-сборка не активирует Tauri-зависимость; потребитель добавляет feature `tauri-host`. Повторного Python/Node runtime, независимого Core или постоянного процесса нет.

Для будущего продукта, уже использующего Tauri 2, добавляется только локальная зависимость с feature:

```toml
iwxa-program-native-port = { path = "<approved-path>/rust-port", features = ["tauri-host"] }
```

В действующем `tauri::Builder` приложение само создаёт `CliPort` из разрешённого манифеста и проверенного пути, сохраняет `Mutex<CliPort>` в своём managed state, и добавляет `iwxa_program_native_port::tauri_host::iwxa_program_connection` в **существующий** `generate_handler!` (у Tauri должен быть один общий `invoke_handler`). WebView по-прежнему использует `createTauriProgramPort(invoke)`. Никаких операций install/remove frontend через этот native port не получает.

**Доказательство**: `qualification/tauri-ipc/tests/ipc_real.rs` использует реальный механизм обработки команды Tauri в **MockRuntime**, но не заменяет команду mock-ответом. Официальный jq 1.8.1 устанавливается через прежний pinned `ManagedArtifactBackend`, затем реальная Rust/Tauri команда `inspect / connect / capabilities / invoke / disconnect / recover` исполняет `6 * 7 = 42` и возвращает JSON. Проверены запрет неизвестной функции и неверной схемы. `qualification/tauri_ipc_smoke.py` удаляет свою тестовую установку через backend и проверяет пользовательский файл. Журнал: `IWXA_TAURI_NATIVE_IPC/1 status=PASS`, `1 passed; 0 failed`, `IWXA_TAURI_NATIVE_IPC_QUALIFICATION/1 status=PASS`.

**Граница приёмки**: реальная нативная команда и IPC PASS, но `mock_runtime=true`, `installed_product_gui=false`. Нативный installed GUI запуск Atlas/Center/Agent и замер памяти/CPU в их конечных сборках ещё не проводились. Полноценный Tauri Wry GUI, внешние OAuth/private GitHub и I4/I5 сюда не включены.

Документальная фиксация — без изменения исходников после проверенного SHA, `[skip ci]`.
