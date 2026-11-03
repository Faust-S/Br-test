# IWXA — Browser Use 0.13.14: публичное испытание реального исполнения

Дата: 2026-11-03. Runner: Ubuntu 24.04, Python 3.12.15; Browser Use 0.13.14.
Точная ревизия теста: 47e6c3a5a0110cb47269bb3f77e838f896036e23.

## Подтверждённый PASS

- Run: https://github.com/Faust-S/Br-test/actions/runs/41643352423
- Evidence artifact: https://github.com/Faust-S/Br-test/actions/runs/41643352423/artifacts/13232974716
- Installed and launched actual upstream Browser Use local Browser API.
- Browser launched with the runner-only settings: chromium_sandbox=False, enable_default_extensions=False, isolated temporary profile.
- A local HTML form was opened in the real browser.
- Negative case: empty required field did not cause POST — PASS.
- Positive case: browser entered IWXA-PUBLIC-TEST and submitted form.
- Independent server-side POST readback exactly [{"reference":"IWXA-PUBLIC-TEST"}] — PASS.
- Browser DOM displayed SAVED — PASS.
- Observed elapsed for this one execution: 2.207 seconds. This is **not** a multi-run benchmark.

Exact summary from journal:
```json
{
  "product":"browser-use",
  "version":"0.13.14",
  "system":"Linux-7.0.0-1017-azure-x86_64-with-glibc2.39",
  "python":"3.12.15",
  "scope":"REAL_BROWSER_SESSION_AND_HTTP_EFFECT_NO_LLM",
  "status":"PASS",
  "model_calls":0,
  "vendor_agent_inference":"NOT_TESTED",
  "negative_empty_submit":"PASS",
  "external_post_readback":"PASS",
  "browser_dom_readback":"PASS",
  "elapsed_seconds":2.207
}
```

## Provenance of failures fixed during the experiment

1. Run 41632226797, initial SHA b04e0cea5f2ff404da0adf2331ee021fe829227f — FAIL in install because browser-use install calls uvx, absent on runner. This was a fixture problem, not evidence of a vendor runtime defect.
2. Run 41643044417, SHA bc6531d125d751b217e70d7a632249f4a3ec258e — browser installed, but process exited before CDP, FIX: pass suitable parameters for headless CI environment.
3. SHA 0df578ca41d24c8787b6cb0e94872a9d9a0acc4d contained an accidental Python escaped-newline edit; subsequent corrected SHA 47e6c3a5a0110cb47269bb3f77e838f896036e23 superseded it. An intermediate run was cancelled; it is not vendor evidence.

## Boundaries and product decision

This proves the local browser execution substrate can perform observable actions through Browser Use on the tested Linux environment; it does **not** prove that a language model independently selected actions, that its predictions were correct, that it operates with the user's models, that it survives restart, or that IWXA Personal Agent can invoke it.

**Current IWXA integration status: I0/feasible technical probe (not I2).** Promote to I2 only after an actual bounded model-driven task with known input/output and a narrow adapter through existing AgentRuntimePort/ToolRuntime. Do not replace any existing browser engine, Core, Task storage, or security controls.

No private IWXA files or secrets were copied to this public repo; no cloud inference or account data were used.
