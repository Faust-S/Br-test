# Independent Aider test on a real public Br-test source module

Copies **existing** `browser-lab/benchmarks/compare_frames.py` at Git blob
`8ea7a15e874b6ae5c169bdb4a31d4e6e16229c55`, plus its unmodified
test suite, into a disposable Git repository. The original working branch
and engine code remain unchanged.

Regression: the module's p95 index currently selects `int(.95 * N)`, which
is one element too high at exact percentile boundaries such as N=20.
The independent added tests expect nearest-rank `ceil(.95*N)-1`. Baseline
must fail before Aider is invoked.

Aider 0.86.0 and local Ollama Qwen2.5-Coder 3B receive that real source
and must produce a nonempty diff affecting only `compare_frames.py`.
All original tests and the two new regression tests must PASS unchanged.
Aider's own exit code or claim that a patch was applied is not sufficient.

No model keys, private Agent source, installation into IWXA, Windows GUI
or production browser engine changes. This is a second distinct coding
task, not evidence of broad project repair reliability.
