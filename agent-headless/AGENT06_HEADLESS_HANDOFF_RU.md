# IWXA Agent 0.6 - Headless Obscura handoff

Public branch: work/2026-11-15-agent06-headless-obscura in Faust-S/Br-test.

## Existing code reused
- Original h4ckf0r0day/obscura at pinned commit 8534b401898ed17b62f69bdc3d5eb186bffb0d60.
- Retained agent-headless/patches/obscura-v0.2.6-agent-origin-gate.patch. This is a transport-origin gate and no-render handshake, NOT a GUI patch or an OS network sandbox.
- Existing ObscuraWorkerController, ObscuraArtifactPreparer, ObscuraManagedBackend and original native JSONL worker.
- Existing Agent ToolProviderPort and U01 installation/lifecycle owners. No new browser engine or Task Engine.

## New public components
- agent-headless/iwxa_obscura/interaction.py: ObscuraInteractiveController with bounded inspect(url, selector), follow_link(url, selector), click_control(url, selector, confirmation_selector, expected_text). The latter is a reviewed DOM action, not attested remote success.
- agent-headless/iwxa_obscura/agent_port.py: opt-in adapter for web.fetch, web.inspect, web.follow_link, web.click_control, with individual descriptor digests, binding digest and provider identity. Private ToolProviderPort result types are imported only when invoked.
- agent-headless/tests/test_interaction.py: protocol and lifecycle tests using a fake subprocess, NOT native browser acceptance.
- agent-headless/qualification/compare_native.py: same-runner upstream versus patched native worker benchmarking of wall time, sampled RSS and CPU, content digests.
- agent-headless/qualification/interactive_native.py: real public HTTPS navigation, actual body DOM reads, same-origin link navigation.
- .github/workflows/agent06-headless-obscura.yml: public Windows 2022 and Ubuntu 24.04 compilation and qualification, no private Agent code or Actions.

## Worker protocol
Native JSONL commands: navigate (with waitUntil), evaluate, dump_text / dump_html, shutdown.
The controller generates narrow JS templates internally. Task input is never interpreted as arbitrary JavaScript. A fresh private temporary profile and worker process is created for each workflow. Every run verifies pinned binary SHA256, requires the IWXA_ORIGIN_NET_GUARD_V2_READY_NO_RENDER greeting, and reaps the worker on exit. A Windows abnormal exit also attempts taskkill /T /F to clean child processes.

## Port and action contract
web.fetch requires exact arguments {url}.
web.inspect requires {url, selector}.
web.follow_link requires {url, selector}.
web.click_control requires {url, selector, confirmation_selector, expected_text}, a nonempty idempotency key, a plan digest, and the independent Agent review/permission decision.
All requests require a matching web_acquisition binding, provider identity and per-tool descriptor digest, and read target=None; consequential target must equal the approved URL. All other providers must delegate to the existing adapter.
Read operations report external_effect_state=NONE, referring to intended provider operation, not a guarantee against side effects of third-party scripts.
A browser click reports external_effect_state=UNKNOWN even with its local DOM postcondition verified. Failed or interrupted clicks after dispatch also return UNKNOWN; do NOT retry automatically. A remote server-side receipt and independent readback are required to call a write accepted.

## Installation
Use existing U01 ManagedArtifactBackend and ObscuraArtifactPreparer with the authoritative pinned archive and worker hash, explicit approval and verified installation receipt. Stage, install, connect, update, recover and remove using existing owner flows. Do not install a second service or add GUI rendering. No unpinned worker may be activated.

## Tests and limitations
Run python -m unittest discover -s agent-headless/tests -p test_*.py -v.
Native qualification must report real Windows worker startup, navigation, DOM and link result, shutdown, cold restart, RSS/CPU, and comparison with original under the same runner. The unpatched original lacks the native origin restriction, so the two builds do not have identical egress policy.
Remote consequential clicks, full Agent Task/Result, and installed Agent GUI are NOT accepted by this component alone.

## Central integrator steps
1. Keep Agent 0.6 branch unchanged until independently merging this opt-in public module.
2. Register the four descriptor digests through current Agent registry and ToolRuntime. Do not create a second Task Engine or permission system.
3. Bind the installed pinned worker through existing U01 approved installation receipts and ToolProviderAdapter routing.
4. Enable web.fetch, web.inspect, web.follow_link after native acceptance. Keep web.click_control disabled until real explicit user approval, idempotency and server-side remote write readback pass.
5. Qualify a real Task: user-selected HTTPS origin, fetch, DOM inspect, same-origin link, actual observed output, Human Review, saved Result, cold reopen and precise receipts.
6. Run controlled Windows end-to-end authorized action, cancellation, timeout, redirect, worker tree cleanup and installed restart scenarios.

Privacy: Br-test contains public code and public numerical results only. Private Agent source, keys, installed product executables and user data must not be uploaded to this repository or its public Actions artifacts.
