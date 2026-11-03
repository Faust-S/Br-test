# Goose 1.55.0 — independent public CLI/provider interface probe

This checks the actual Goose 1.55.0 Linux CLI binary through a deterministic **fake** OpenAI-compatible localhost HTTP endpoint. There is no downloaded LLM, no model quality claim, no paid API, and no IWXA private code.

The workflow downloads the precise official v1.55.0 Linux GNU CLI release and verifies the GitHub release SHA-256 digest `d9afefe7b8f4e372ebefd8b465ab6eea1f297304d058e8e9379ed627e0329708`; then checks `goose --version`, `goose run --help`, and one real CLI invocation against the local dummy model API. Real vendor CLI + synthetic response is **not** real autonomous inference, task reasoning, or private IWXA AgentRuntimePort qualification.

The simulated server handles OpenAI-compatible chat completion (including SSE) and records which model route Goose actually called. The test exits nonzero on failed version, no local model request, malformed/failed CLI, or missing reply marker. Evidence artifact includes exact exit codes, errors and HTTP observations.

Upstream references:
- https://github.com/aaif-goose/goose/releases/tag/v1.55.0
- https://goose-docs.ai/docs/guides/running-tasks/
- https://goose-docs.ai/docs/getting-started/providers/

Next real requirement: execute a bounded multi-step task with an actual selected local LLM and confirm material file/result in Task/Result via the existing IWXA adapter. Do not equate this fake protocol fixture with that result.
