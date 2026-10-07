# Verification — 7 October 2026

## Verified locally

- Project test suite: 99 tests, 0 failures, 0 errors. See `project_tests.xml`.
- Ruff correctness/lint checks passed for the application and command-line entry points.
- `pip check`: no broken installed requirements (the host has an unrelated invalid-distribution warning).
- Streamlit startup and HTTP health endpoint: OK at http://127.0.0.1:8501.
- Streamlit AppTest: main dashboard renders all five tabs; blind review route renders without rankings or preselected scores.
- Browser check: overview and evaluation tabs render the saved dataset and baseline charts.
- Dataset integrity: 70 unique candidate IDs and 70 corresponding historical labels; source readable for all candidates.
- MiniLM/Chroma retrieval: 70 queries, exactly five neighbours each, zero self-matches. This is a structural check, not human-labelled retrieval relevance.
- Target Requests baseline: 591 passed, 2 failed, 0 errors; failures recorded in `data/requests_baseline_summary.json`.
- Docker Compose configuration validates. The image has not been built or run on this machine.

## Concrete blockers

1. OpenAI generation returned HTTP 429, `credit_balance_exhausted` / `insufficient_quota`. Listing models succeeded but did not establish usable inference credit. Both attempted ranking runs produced zero successful outputs. No fabricated model scores were substituted.
2. No independent expert ratings exist. Two or three real developers must complete the blind form. Synthetic ratings are not a substitute.
3. Docker Desktop 4.67.0 fails before its engine starts: its inference-manager Unix socket at `%LOCALAPPDATA%/Docker/run/dockerInference` cannot be accessed. A non-destructive attempt to preserve/rename that stale socket also failed. Docker was not reset and no Docker data was deleted. The local Streamlit launcher remains usable.

## Implemented hardening

- OpenAI and Anthropic provider support, validated structured responses, bounded network timeouts/retries, safe error messages and quota-aware stopping.
- Atomic thread-safe cache writes, corrupt-cache recovery, per-run prompt/dataset fingerprints, separate pilot outputs and preservation of earlier rankings on a failed rerun.
- Matched three-agent context/no-context orchestration; incomplete or stale rankings excluded from full-dataset comparison.
- Isolated refactoring branches without overwriting previous worktrees; signature/decorator preservation; rejection of extra executable statements and helper collisions.
- JUnit-based test identity comparison rejects lost tests, skipped prior passes, crashes, collection errors and new failures. Ruff and duplication may not worsen.
- Corrected paired-comparison sample alignment and Kendall agreement tie correction.
- Validated human ratings, a dedicated blind form, no automatic default urgency scores, persistent reviewer files.
- Portable licensed source snapshot, pinned direct dependencies, Dockerfile, hardened local Compose service, health check, CI verification workflow, local launcher and offline report.

## Scope

This is a hardened local research/demo application with a Docker package. It is not a certified public production service. The real LLM/refactoring experiments, independent human validation and actual container build/smoke test remain pending. Public deployment additionally requires authentication, TLS, operational monitoring, backups and an environment-specific security/load review.

Use `DEPLOYMENT.md` for exact launch and experiment commands, `READINESS.md` for the evidence audit, and `PRESENTATION_NOTES.md` for an honest presentation outline.
