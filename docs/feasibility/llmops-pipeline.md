# Feasibility: LLMOps pipeline (CI gate + eval ledger + pre-push guard)

**Motivation:** The reliability pieces (eval harness, Langfuse tracing, judge
backfill, flagged-trace pull) all exist but run only when a human remembers to
run them; nothing enforces them, so a prompt/model regression can ship unnoticed.

**Problem:** A prompt edit is a production deploy, but there is no CI, no git
hook, and no over-time record of eval quality. The eval needs local PII gold
data and the live rate-limited model, so it cannot run in cloud CI the normal
way, and a single eval run is a test, not observability.

**Solution:** A two-plane setup. Deterministic checks (pytest, lint, frontend
build/e2e, prompt-schema drift) run in GitHub Actions. The eval gate runs
locally as a pre-push hook (`run_eval.py --strict`), blocking regressions
without shipping data anywhere. A committed, aggregate-only `eval/baseline.json`
ledger records accuracy over time (numbers only, no message IDs, no email
content, no company names).

**Changes:** `.github/workflows/ci.yml`; `scripts/install-hooks.sh` +
`scripts/hooks/pre-push`; `evaluation/scoring.py` gains `report_to_ledger` /
`append_ledger` (aggregate-only); `run_eval.py` gains `--ledger`;
`eval/baseline.json` (committed). Docs: CLAUDE.md + README LLMOps table.

**Benefits:** A prompt change is treated like a risky deploy: gated on evals,
its quality tracked as a committed time series, with zero real data leaving the
machine (public repo stays PII-free).
