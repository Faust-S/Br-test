# n8n 2.46.4: real server-restart qualification

Standalone public source and GitHub Actions for a real self-hosted n8n webhook
which remains published and callable after the entire server container stops
and a **new** container starts with the same disposable SQLite user folder.

The test imports and publishes the workflow **once**, then performs one
observable two-step HTTP operation before restart and a **different operation
key** after restart. An independent loopback HTTP receiver must observe
exactly two effects per invocation, no unsolicited duplicates during restart,
and the published endpoint must return HTTP 200 in both phases.

This tests server-level workflow persistence, **not** replay of an in-progress
workflow, exactly-once effects, end-to-end Personal Agent Task/Result or the
private N8NWorkflowV1 adapter. A repeated identical idempotency key was
already proven to duplicate effects in the separate public webhook trial.

- Exact vendor image: docker.n8n.io/n8nio/n8n:2.46.4
- Local disposable runner profile, no secrets, no private IWXA code or accounts
- One Linux job, 14 min cap, no background tasks and no extra GitHub resources
  after the workflow concludes
- Published webhook and per-phase independent server readback evidence
