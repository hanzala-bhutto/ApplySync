"""Pull emails flagged wrong in Langfuse (by a human, or the LLM-judge
evaluators) into the eval gold dataset for re-review, closing the loop
between "this looked wrong" and the eval harness (see build_eval_dataset.py)
actually tracking it.

Convention: three Boolean score configs (Settings > Scores, once) -
relevance_correct, extraction_correct, disambiguation_correct - one per
pipeline stage, mirroring the eval harness's own per-stage metrics. Set to
false either manually in the UI (optionally with a comment noting what should
have been extracted instead) or automatically by an LLM-as-judge evaluator
(see docs/feasibility/langfuse-llm-judge.md; evaluators here are run
manually/on-demand via the UI's Actions -> Evaluate, not live, to keep judge
LLM calls from contending with the pipeline's own rate-limited sync traffic).
This script finds every trace scored false on any of the given score names,
pulls its source email and the pipeline's own (wrong) output, and writes it
into eval/samples/gold.json as an UNVERIFIED sample (mirroring
build_eval_dataset.py's prefill: a flag is a reason to look again, not a
ground-truth label by itself - a human still has to open the file and write
the correct labels).

Only LangGraph-level traces (the ones with an `email` key in their input,
i.e. one full email's run through the pipeline) are pulled; a scored trace
for a standalone call (e.g. company research) is skipped since it has no
eval-comparable shape.

Usage (from repo root, venv active, LANGFUSE_PUBLIC_KEY/SECRET_KEY set):
    python backend/scripts/pull_flagged_traces.py \
        [--score-name relevance_correct extraction_correct disambiguation_correct] \
        [--out PATH]
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import httpx

from applysync.config import get_settings
from applysync.evaluation import EvalSample, load_all_samples, save_samples

DEFAULT_OUT = Path("eval/samples/gold.json")
DEFAULT_SCORE_NAMES = ["relevance_correct", "extraction_correct", "disambiguation_correct"]


def _fetch_flagged_trace_ids(
    client: httpx.Client, score_names: list[str]
) -> dict[str, dict[str, str | None]]:
    """Returns {trace_id: {flagged_score_name: comment_or_None}} for every
    BOOLEAN score named any of score_names with value=false. A trace flagged
    on more than one stage keeps all of them, since that's useful triage
    signal (e.g. both extraction_correct and disambiguation_correct false
    means the bad extraction likely caused the bad match too)."""
    trace_flags: dict[str, dict[str, str | None]] = defaultdict(dict)
    for score_name in score_names:
        cursor: str | None = None
        while True:
            params = {
                "name": score_name,
                "dataType": "BOOLEAN",
                "value": "false",
                "fields": "subject,details",
                "limit": 100,
            }
            if cursor:
                params["cursor"] = cursor
            response = client.get("/api/public/v3/scores", params=params)
            response.raise_for_status()
            payload = response.json()
            for score in payload.get("data", []):
                subject = score.get("subject") or {}
                trace_id = subject.get("traceId") or (
                    subject.get("id") if subject.get("kind") == "trace" else None
                )
                if trace_id:
                    trace_flags[trace_id][score_name] = score.get("comment")
            cursor = (payload.get("meta") or {}).get("cursor")
            if not cursor:
                break
    return dict(trace_flags)


def _prefill_labels_from_trace_output(output: dict) -> dict:
    labels = {
        "is_relevant": output.get("classification") == "relevant",
        "company_name": None,
        "job_title": None,
        "status": None,
    }
    extracted = output.get("extracted") or {}
    if extracted:
        job_title = extracted.get("job_title")
        labels["company_name"] = extracted.get("company_name")
        labels["job_title"] = None if job_title == "(unspecified role)" else job_title
        labels["status"] = extracted.get("status")
    return labels


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--score-name", nargs="+", default=DEFAULT_SCORE_NAMES)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    settings = get_settings()
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        raise SystemExit(
            "LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY not set in .env - nothing to pull."
        )

    existing = {}
    if args.out.exists():
        existing = {s.message_id: s for s in load_all_samples(args.out)}
    print(f"existing samples: {len(existing)}")

    with httpx.Client(
        base_url=settings.langfuse_host,
        auth=(settings.langfuse_public_key, settings.langfuse_secret_key),
        timeout=15.0,
    ) as client:
        trace_flags = _fetch_flagged_trace_ids(client, args.score_name)
        print(f"found {len(trace_flags)} trace(s) flagged false on {', '.join(args.score_name)}")

        pulled = 0
        skipped_no_email = 0
        flags_to_show = []
        for trace_id, flags in trace_flags.items():
            response = client.get(f"/api/public/traces/{trace_id}", params={"fields": "core,io"})
            response.raise_for_status()
            trace = response.json()

            email = (trace.get("input") or {}).get("email")
            if not isinstance(email, dict) or "message_id" not in email:
                skipped_no_email += 1
                continue

            labels = _prefill_labels_from_trace_output(trace.get("output") or {})
            existing[email["message_id"]] = EvalSample(
                message_id=email["message_id"],
                sender=email.get("sender", ""),
                subject=email.get("subject", ""),
                date=email.get("date", ""),
                body=email.get("body", ""),
                label_is_relevant=labels["is_relevant"],
                label_company=labels["company_name"],
                label_title=labels["job_title"],
                label_status=labels["status"],
                verified=False,
            )
            pulled += 1
            flags_to_show.append((email["message_id"], flags))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    save_samples(args.out, list(existing.values()))
    print(f"wrote {len(existing)} samples to {args.out} ({pulled} pulled, {skipped_no_email} skipped)")

    if flags_to_show:
        print("\nflagged stage(s) and any annotation comments (use these while correcting labels):")
        for message_id, flags in flags_to_show:
            stage_summary = ", ".join(
                f"{name}" + (f" ({comment})" if comment else "") for name, comment in flags.items()
            )
            print(f"  {message_id}: {stage_summary}")

    if pulled:
        print(
            "\nnext: review each pulled sample's labels in the file, correct any that "
            'are wrong, and set "verified": true - run_eval.py only scores verified samples.'
        )


if __name__ == "__main__":
    main()
