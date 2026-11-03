# IWXA — Aider 0.86.0: actual local model, Git patch and independent test

Independent public trial of a **specialized coding program** rather than a
general agent. Versions are pinned: Aider **0.86.0** and Ollama Docker engine
**0.40.14**; model **qwen2.5-coder:1.5b**, with resolved model digest recorded
from the Ollama API.

A disposable miniature Python Git project has a real regression: a percentage
invoice discount incorrectly includes delivery costs. Seven regression tests
cover no discount, mixed amounts, integer rounding, full discount, zero
subtotal and invalid inputs. Baseline tests **must fail**, then the actual
installed Aider CLI is allowed to edit **only** `src/invoice.py`. We check:
nonempty `git diff`; exact modified tracked path; unchanged tests; successful
unittest and independently re-imported function outputs. A claim by Aider
that it completed work is *not* sufficient.

This is a genuine local model inference and source-code-edit trial on a
**self-contained regression fixture**. It is *not* a repair of an upstream
issue in a third-party project and is not a Windows Desktop or IWXA
Personal Agent integration test. A single run does not establish reliability
or production readiness. Aider 0.86.0's latest GitHub release was 2025-08-09;
later source updates must not be confused with this tested release.

No credentials, private IWXA sources, cloud model, real user data or costly
continuous jobs. One Ubuntu job, maximum 18 minutes; no automatic retries.

Official sources:
- https://github.com/Aider-AI/aider/releases/tag/v0.86.0
- https://aider.chat/docs/scripting.html
- https://aider.chat/docs/llms/ollama.html
- https://ollama.com/library/qwen2.5-coder:1.5b
