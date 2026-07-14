"""Replay the gold dataset through the real pipeline stages and score it.

Runs each verified sample through the ACTUAL scrutiny and classify+extract
nodes with the real NVIDIA model (honoring the 40 RPM client-side limiter in
get_chat_model), then scores per stage - see
backend/applysync/evaluation/scoring.py for the metric definitions and
docs/feasibility/eval-harness.md for why the metrics are per-stage.

This is the regression gate CLAUDE.md's LLM section describes as a manual
5-email ritual: run it BEFORE and AFTER any prompt or model change, and
compare. ~150 samples take a few minutes at the rate limit.

Usage (from repo root, venv active):
    python eval/run_eval.py [--samples eval/samples/gold.json]
                            [--include-unverified] [--limit N] [--strict]

--strict exits nonzero when thresholds fail, for use as a pre-merge check.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from applysync.config import get_settings, get_sources
from applysync.evaluation import StagePrediction, Thresholds, format_report, score_samples
from applysync.evaluation.scoring import load_samples
from applysync.gmail.models import RawEmail
from applysync.llm import get_chat_model
from applysync.pipeline.nodes import make_classify_and_extract_node, make_scrutinize_relevance_node

DEFAULT_SAMPLES = Path("eval/samples/gold.json")


def run_pipeline_stages(samples, model, sources) -> dict[str, StagePrediction]:
    scrutinize = make_scrutinize_relevance_node(model, sources)
    classify_and_extract = make_classify_and_extract_node(model, sources)

    predictions: dict[str, StagePrediction] = {}
    for i, sample in enumerate(samples, start=1):
        email = RawEmail(
            message_id=sample.message_id,
            thread_id=f"eval-{sample.message_id}",
            sender=sample.sender,
            subject=sample.subject,
            date=sample.date,
            body=sample.body,
        )

        scrutiny = scrutinize({"email": email})["scrutiny"]
        pred = StagePrediction(message_id=sample.message_id, scrutiny=scrutiny)

        if scrutiny == "pass":
            output = classify_and_extract({"email": email})
            pred.classification = output.get("classification")
            extracted = output.get("extracted")
            if extracted is not None:
                pred.company = extracted.company_name
                pred.title = extracted.job_title
                pred.status = extracted.status

        predictions[sample.message_id] = pred
        if i % 10 == 0 or i == len(samples):
            print(f"  {i}/{len(samples)} evaluated", flush=True)

    return predictions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, default=DEFAULT_SAMPLES)
    parser.add_argument(
        "--include-unverified",
        action="store_true",
        help="also score samples whose labels have not been human-verified "
        "(measures the pipeline against its own pre-filled output - only "
        "useful for smoke-testing the harness itself)",
    )
    parser.add_argument("--limit", type=int, default=None, help="only run the first N samples")
    parser.add_argument("--strict", action="store_true", help="exit 1 if thresholds fail")
    args = parser.parse_args()

    samples = load_samples(args.samples, include_unverified=args.include_unverified)
    if args.limit:
        samples = samples[: args.limit]
    if not samples:
        print(
            f"no {'samples' if args.include_unverified else 'verified samples'} in {args.samples} - "
            "run backend/scripts/build_eval_dataset.py, then review labels and set verified: true"
        )
        return 1

    print(f"running {len(samples)} samples against the real model (rate-limited)...")
    settings = get_settings()
    predictions = run_pipeline_stages(samples, get_chat_model(settings), get_sources())

    report = score_samples(samples, predictions)
    thresholds = Thresholds()
    print()
    print(format_report(report, thresholds))

    if args.strict and not thresholds.passed(report):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
