# Eval gold dataset

`gold.json` lives here but is **gitignored**: every sample contains a real
email body from the user's inbox (PII that must never land on GitHub). It's
a pretty-printed JSON array (not JSONL) with `verified`/`labels` before the
body in each record, so it's directly reviewable/editable in a text editor.

To (re)build it on this machine:

```
python backend/scripts/build_eval_dataset.py
```

Labels are pre-filled from the pipeline's own stored output, so they start
as machine guesses. Review each sample, correct any wrong label, and set
`"verified": true` - `eval/run_eval.py` only scores verified samples by
default, so unreviewed pre-fills can never silently become ground truth.

Label conventions:
- `job_title: null` means the email genuinely never states the role; the
  pipeline is scored correct for it only when it emits `(unspecified role)`.
- `is_relevant` is whether a real, submitted application update is described
  (job-alert digests, drafts, marketing are all `false`).

Re-running the builder preserves existing samples (including verified flags
and hand-corrected labels); it only appends message ids not yet present.
