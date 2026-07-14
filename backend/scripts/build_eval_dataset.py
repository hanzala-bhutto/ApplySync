"""Build (or extend) the eval gold dataset from the real inbox + live DB.

Pre-fills each sample's labels from what the pipeline itself already stored
(processed_emails.classification, and the raw_extract_json on the status
event the email produced), so human labeling is a CORRECTION pass over
eval/samples/gold.json - review each sample, fix any wrong label, and flip
"verified" to true - rather than labeling from scratch. run_eval.py only
scores verified samples by default, so an unreviewed pre-fill can never
silently become ground truth (that would just measure the pipeline against
its own output). Written as a pretty-printed JSON array (not JSONL) with
labels/verified before the body in each record, specifically so it's
reviewable/editable directly in a text editor - a wall of one-line-per-
record JSON is unreadable once a record holds a multi-paragraph email.

Samples contain real email bodies (PII): eval/samples/ is gitignored, and
this script exists so the dataset is regenerable on this machine instead of
ever being committed.

Re-running is safe: samples already in the output file are preserved as-is
(including their verified flag and any hand-corrected labels); only message
ids not yet present are added.

Usage (from repo root, venv active):
    python backend/scripts/build_eval_dataset.py [--limit 150] [--db PATH]
"""
from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

from sqlmodel import Session, select

from applysync.config import get_settings
from applysync.db import repository as repo
from applysync.db.init_db import get_engine
from applysync.db.models import ProcessedEmail
from applysync.evaluation import EvalSample, load_all_samples, save_samples
from applysync.gmail.client import GmailClient

DEFAULT_OUT = Path("eval/samples/gold.json")


def pick_message_ids(session: Session, limit: int) -> list[str]:
    """Stratified across classification values (relevant, irrelevant,
    scrutiny_rejected, extraction_failed) by round-robin, so the dataset
    exercises the reject paths too instead of only happy-path extractions.
    Deterministic for a given DB state (seeded shuffle) so re-runs add the
    same samples."""
    by_classification: dict[str, list[str]] = defaultdict(list)
    for row in session.exec(select(ProcessedEmail).order_by(ProcessedEmail.message_id)).all():
        by_classification[row.classification].append(row.message_id)

    rng = random.Random(0)
    for ids in by_classification.values():
        rng.shuffle(ids)

    picked: list[str] = []
    groups = sorted(by_classification)
    while len(picked) < limit and any(by_classification[g] for g in groups):
        for group in groups:
            if by_classification[group] and len(picked) < limit:
                picked.append(by_classification[group].pop())
    return picked


def prefill_labels(session: Session, message_id: str) -> dict:
    processed = session.get(ProcessedEmail, message_id)
    labels = {
        "is_relevant": processed is not None and processed.classification == "relevant",
        "company_name": None,
        "job_title": None,
        "status": None,
    }
    event = repo.find_status_event_by_source_email(session, message_id)
    if event is not None and event.raw_extract_json:
        extract = json.loads(event.raw_extract_json)
        labels["company_name"] = extract.get("company_name")
        job_title = extract.get("job_title")
        # The stored sentinel means "email never states the role"; the label
        # convention for that is null (see scoring._titles_match).
        labels["job_title"] = None if job_title == "(unspecified role)" else job_title
        labels["status"] = extract.get("status")
    return labels


def load_existing(path: Path) -> dict[str, EvalSample]:
    if not path.exists():
        return {}
    return {sample.message_id: sample for sample in load_all_samples(path)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=150, help="total samples to target")
    parser.add_argument("--db", type=Path, default=None, help="path to the SQLite db")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    settings = get_settings()
    db_path = args.db or settings.db_path

    existing = load_existing(args.out)
    print(f"existing samples preserved: {len(existing)}")

    with Session(get_engine(db_path)) as session:
        candidate_ids = pick_message_ids(session, args.limit)
        new_ids = [mid for mid in candidate_ids if mid not in existing]
        print(f"fetching {len(new_ids)} new emails from Gmail...")

        client = GmailClient(settings)
        emails = client.fetch_messages_by_id(new_ids)

        added = 0
        for email in emails:
            labels = prefill_labels(session, email.message_id)
            sample = EvalSample(
                message_id=email.message_id,
                sender=email.sender,
                subject=email.subject,
                date=email.date,
                body=email.body,
                label_is_relevant=labels["is_relevant"],
                label_company=labels["company_name"],
                label_title=labels["job_title"],
                label_status=labels["status"],
                verified=False,
            )
            existing[email.message_id] = sample
            added += 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    save_samples(args.out, list(existing.values()))

    verified = sum(1 for s in existing.values() if s.verified)
    print(f"wrote {len(existing)} samples to {args.out} ({added} new, {verified} verified)")
    if verified < len(existing):
        print(
            "next: review each sample's labels in the file, correct any that are wrong, "
            'and set "verified": true - run_eval.py only scores verified samples.'
        )


if __name__ == "__main__":
    main()
