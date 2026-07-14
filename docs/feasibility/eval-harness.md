# Eval harness and gold dataset

## Motivation
Reliability is now the project's stated focus, and nothing can be called reliable (or improved) without a number: every accuracy bug so far was found by a human eyeballing the dashboard, and every prompt/model change is verified by an ad-hoc manual 5-email check.

## Problem
There is no measurement. The pipeline's known-fragile stages (scrutiny filtering, classify+extract, matching) have no accuracy baseline, so a prompt or model change can silently regress extraction (as the nano-model switch once did, corrupting 174 real rows) and nothing catches it until real data is damaged.

## Solution
A labeled gold dataset built from the user's real inbox (pre-filled from the pipeline's own stored outputs in `processed_emails`/`raw_extract_json`, then human-verified, so labeling is a correction pass rather than from-scratch work) plus a runner that replays every sample through the real scrutiny and classify+extract nodes and scores per stage: scrutiny false-reject rate, relevance-classification accuracy, and per-field extraction accuracy (company/title normalized the same way matching normalizes, status exact). Samples containing real email bodies stay out of git (PII); only the harness code is committed.

## Changes
- `backend/applysync/evaluation/scoring.py` (pure scoring logic, unit-testable without any LLM)
- `backend/scripts/build_eval_dataset.py` (export + pre-label from the live DB and Gmail)
- `eval/run_eval.py` (CLI runner against the real model, honoring the 40 RPM limiter)
- `.gitignore`: exclude `eval/samples/` contents
- `tests/test_eval_scoring.py`

## Benefits
- Turns the documented-but-manual "re-run the accuracy check before trusting a prompt/model change" rule into a repeatable command with pass/fail thresholds.
- Gives the reliability work that follows (confidence routing, tiered models, prompt changes) a baseline to prove improvement against, per stage rather than one blended number.
