# Goose 1.55.0 with actual local Ollama model

Separate bounded experiment. Goose real CLI release SHA-256 is verified against upstream; Ollama server uses published image tag `0.40.14`, and downloads model `qwen2.5:0.5b` from official Ollama model registry. Input files are generated synthetic data; no user source, no API key and no cloud inference.

This **is** a real CPU-based model inference test, unlike the preceding fake OpenAI-compatible endpoint. The task requires independently materializing `result.txt` with exactly `12\n`, based on two values read from a real input file. Success requires both CLI exit=0 and independently verified final file content. If the tiny model cannot use Goose tools reliably, the test must FAIL and the failure must be kept; a passing package install or plausible assistant text is not acceptable as success.

Constraints: one Ubuntu 24.04 job, 17-minute cap, no automatic retry, one small model, disposable working directory, no production integrations. The model's performance/quality is **not representative** of stronger Ollama models. In particular failure of this test does not mean every Goose model route fails.

The Docker image's mutable tag is recorded, model digest is captured via `/api/tags`. Future reproducibility benefits from recording resolved Docker image SHA-256. The experiment does not test the private proposal-only `GooseLocalCliAdapter`, user interface or Task/Result persistence.
