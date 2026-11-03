# IWXA Agent Platforms: n8n 2.46.4, independent real workflow execution

**Verified:** 2026-11-03. **Source checkout:** 02d5293c70e81b0ea2ad18ae510bb74f802ab7e7.

## Direct evidence

- Workflow SUCCESS: https://github.com/Faust-S/Br-test/actions/runs/41647669193
- JSON artifact: https://github.com/Faust-S/Br-test/actions/runs/41647669193/artifacts/13235274774
- OS: Ubuntu 24.04 runner, reported Linux-7.0.0-1017-azure-x86_64-with-glibc2.39, Python 3.12.15.
- Vendor: **official n8n Docker image** docker.n8n.io/n8nio/n8n:2.46.4, version verified by actual executable.
- No LLM inference, account, private IWXA code, production webhook or external data.

| Test | Actual observation | Status |
|---|---|---|
| Four Python fixture unit tests | 4/4 complete, alone not vendor proof | PASS |
| Real n8n CLI version | 2.46.4, exit 0 | PASS |
| Import positive workflow to temporary SQLite | exit 0, separate process | PASS |
| Execute positive two-step workflow | exit 0, 3.145s | PASS |
| Independent HTTP receiver records | POST /ok/1 and POST /ok/2 in order, exactly once each | PASS |
| Import negative workflow | exit 0, separate process | PASS |
| Execute two-step workflow with intentional HTTP 503 on step 2 | exit 1, 3.112s | PASS (expected failure recognized) |
| Independent HTTP receiver records | POST /fail/1 and POST /fail/2, in order, exactly once each | PASS |
| Different n8n CLI processes see same imported workflow | import and execute are different processes with one temporary profile | PASS |
| Engine restart recovery / schedules / production webhook | Not part of test | NOT_TESTED |
| Direct IWXA n8n workflow adapter | Not invoked | NOT_TESTED |

The 503 node returned a real error and n8n reported `Service unavailable - try again later`. The test is not a benchmark; 3.145/3.112 seconds are single observations of the workflow execution CLI, not median response time.

## Installation and test environment lessons

- Initial public run https://github.com/Faust-S/Br-test/actions/runs/41646999851 failed at npm install with **EBADPLATFORM**, requesting `@napi-rs/canvas-linux-arm64-musl@1.0.11` on Linux x64/glibc.
- Second npm run https://github.com/Faust-S/Br-test/actions/runs/41647260327 failed identically despite `--omit=optional --legacy-peer-deps`.
- Rather than changing n8n internals or testing the wrong version, switched to the official n8n image 2.46.4 on a disposable runner. The exact tag was version-checked at runtime. This is an installation-route observation, **not** a conclusion that npm installation is impossible on supported hosts.

## Product decision

The actual n8n engine is qualified for **this narrow Linux CLI two-step HTTP execution scenario**; it is not qualified as a Personal Agent integration. Private IWXA source inspection shows `N8NWorkflowV1.execute` sends a POST to configured webhook route and maps a 2xx result to ACKNOWLEDGED, while unknown network/non-2xx outcomes must be handled as UNKNOWN. This interface is different from the n8n server CLI used in this trial.

Next meaningful trial: a disposable n8n server with a published webhook, invoked via the existing interface shape, then a genuine end-to-end ToolRuntime Task/Result/receipt readback in the private checkout. No separate scheduler, no new program installer, and no undocumented assumptions about successful external effects.
