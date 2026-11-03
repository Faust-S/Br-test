# Public execution experiment: Browser Use 0.13.14

Independent public reproducibility experiment for IWXA Agent Platforms & Execution Systems.
No private IWXA Personal Agent source, configuration, credentials or models are used.

Workflow: [.github/workflows/browser-use-execution.yml](../../../.github/workflows/browser-use-execution.yml).
Probe: [probe.py](probe.py).

**What is really tested:** exact PyPI Browser Use version, browser startup through its public Python API,
headless local browser, DOM interaction (negative required-field and positive text entry),
a real local HTTP POST, server readback, and browser DOM readback.

**What is not tested:** autonomous agent decisions, LLM or model compatibility, real cloud/local
inference cost, GUI appearance in the IWXA desktop, IWXA private adapters, cross-process
recovery, and Windows GUI. Result is only a Browser Use library execution smoke.

Single Ubuntu job, timeout 15 min, no model credentials, no automatic retry and only
short-lived results. This work does not modify the browser's product source branch.

The use of the upstream browser dependency is **specific to evaluating Browser Use**.
It is not a test of IWXA Obscura Browser, and does not change its engine policy.
