# n8n 2.46.4 — independent IWXA execution research

Separate public trial of the real self-hosted n8n executable, without model, account, or IWXA private sources.
Pinned n8n release: https://github.com/n8n-io/n8n/releases/tag/n8n%402.46.4 .
Official server CLI guide: https://docs.n8n.io/hosting/cli-commands/ .
n8n is fair-code (Sustainable Use License); this trial does not claim unrestricted redistribution rights.

Two scenarios: manual-trigger workflow with two sequential real HTTP POST nodes and independent local server readback; same flow with intentional HTTP 503 on second node, verifying that error is visible and no extra request is sent. Workflow definition is imported via n8n CLI, and execution happens via separate CLI process with a disposable SQLite profile. This establishes persisted definition across separate CLI invocations, **not** full process restart recovery.

The real n8n run has a JSON evidence artifact. There are also four standard-library unit tests for the public test fixture, but those are **not** a claim that n8n works. No installed Personal Agent adapter, model inference, remote cloud, Windows GUI, and user private files are exercised. CI uses one Ubuntu 24.04 runner, Node 24, Python 3.12, 16-minute maximum.

To run on an n8n-supported local environment with the version-pinned n8n 2.46.4 CLI on PATH:

```sh
python -m unittest discover -s research/agent-platforms/n8n -p 'test_*.py' -v
python research/agent-platforms/n8n/probe.py
```

## Runner installation decision
Two independent npm installation attempts failed with EBADPLATFORM for @napi-rs/canvas-linux-arm64-musl@1.0.11 on Ubuntu x64, even with --omit=optional/--legacy-peer-deps. This is a dependency/packaging blocker of that npm method, not evidence of workflow correctness. To avoid altering n8n, the public test uses the upstream published Docker image 2.46.4 and isolated temporary profile. Host-networking is limited to this disposable Linux CI trial to communicate with the loopback HTTP fixture. Not an IWXA deployment recipe.
