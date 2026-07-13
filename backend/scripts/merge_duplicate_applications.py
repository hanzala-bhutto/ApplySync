"""One-time cleanup: merge application rows that are the SAME application split
across different platform values (and/or casing/applied_date), now that
matching treats platform as attribution rather than identity
(see repository.find_matching_application).

Groups applications by their match identity - normalized company + normalized
title (the same _normalize_for_matching the matcher uses) - and collapses each
group of 2+ into a single canonical row:

  - canonical = the lowest id in the group (the oldest row, matching how
    find_matching_application picks "oldest wins"),
  - every StatusEvent from the other rows is reassigned to the canonical,
  - the canonical's applied_date becomes the EARLIEST in the group (the true
    first-applied date),
  - the canonical's current_status is recomputed from the LATEST status event
    (by event_date) across the merged set,
  - the now-empty duplicate rows are deleted.

This only merges rows the matcher already considers identical (same company +
title). Title/company-string variants (e.g. "Backend Engineer (m/w/d)" vs
"(Senior) Backend Engineer", "Galvany" vs "Galvany Energy") are a DIFFERENT
problem - the disambiguation agent and company-alias canonicalization - and are
deliberately left untouched here.

Dry-run by default (prints the plan, changes nothing). Pass --apply to perform
the merge. Point at a specific DB with --db PATH (defaults to the configured
db_path).
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from sqlmodel import Session, select

from applysync.config import get_settings
from applysync.db import repository as repo
from applysync.db.init_db import get_engine
from applysync.db.models import Application, StatusEvent


def _identity(app: Application) -> tuple[str, str]:
    return (
        repo._normalize_for_matching(app.company_name),
        repo._normalize_for_matching(app.job_title),
    )


def find_duplicate_groups(session: Session) -> list[list[Application]]:
    groups: dict[tuple[str, str], list[Application]] = defaultdict(list)
    for app in session.exec(select(Application).order_by(Application.id)).all():
        groups[_identity(app)].append(app)
    return [g for g in groups.values() if len(g) > 1]


def merge_group(session: Session, group: list[Application], *, apply: bool) -> dict:
    group = sorted(group, key=lambda a: a.id)
    canonical = group[0]
    dupes = group[1:]

    all_events: list[StatusEvent] = []
    for app in group:
        all_events.extend(
            session.exec(select(StatusEvent).where(StatusEvent.application_id == app.id)).all()
        )
    latest = max(all_events, key=lambda e: e.event_date) if all_events else None
    earliest_applied = min(a.applied_date for a in group)

    plan = {
        "canonical_id": canonical.id,
        "canonical_name": canonical.company_name,
        "canonical_title": canonical.job_title,
        "merged_ids": [a.id for a in dupes],
        "merged_names": sorted({a.company_name for a in group}),
        "merged_platforms": sorted({a.platform for a in group}),
        "final_status": latest.status if latest else canonical.current_status,
        "final_applied_date": str(earliest_applied),
        "event_count": len(all_events),
    }

    if apply:
        for dupe in dupes:
            for event in session.exec(
                select(StatusEvent).where(StatusEvent.application_id == dupe.id)
            ).all():
                event.application_id = canonical.id
                session.add(event)
        canonical.applied_date = earliest_applied
        if latest is not None:
            canonical.current_status = latest.status
        canonical.updated_at = repo._utcnow()
        session.add(canonical)
        session.flush()  # reassign events before deleting their old parents
        for dupe in dupes:
            session.delete(dupe)
        session.commit()

    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="perform the merge (default: dry-run)")
    parser.add_argument("--db", type=Path, default=None, help="path to the SQLite db")
    args = parser.parse_args()

    db_path = args.db or get_settings().db_path
    print(f"DB: {db_path}   mode: {'APPLY' if args.apply else 'DRY-RUN'}")

    with Session(get_engine(db_path)) as session:
        groups = find_duplicate_groups(session)
        if not groups:
            print("No same-identity duplicate groups found. Nothing to merge.")
            return
        total_removed = 0
        for group in sorted(groups, key=lambda g: min(a.id for a in g)):
            plan = merge_group(session, group, apply=args.apply)
            total_removed += len(plan["merged_ids"])
            print("-" * 70)
            print(f"  keep #{plan['canonical_id']}  {plan['canonical_name']!r} / {plan['canonical_title']!r}")
            print(f"  merge ids {plan['merged_ids']}  (platforms: {plan['merged_platforms']})")
            print(f"  names seen: {plan['merged_names']}")
            print(f"  -> status={plan['final_status']} applied={plan['final_applied_date']} events={plan['event_count']}")
        print("=" * 70)
        verb = "removed" if args.apply else "would remove"
        print(f"{len(groups)} group(s); {verb} {total_removed} duplicate row(s).")
        if not args.apply:
            print("Re-run with --apply to perform the merge.")


if __name__ == "__main__":
    main()
