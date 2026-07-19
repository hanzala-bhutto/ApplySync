"""Replay specific historical disambiguate_match observations through the
CURRENT disambiguate.py code (not just re-judge the old trace output) - used
to verify the date-arithmetic fix (see docs/feasibility, the LLM-judge
accuracy pass) actually changes the agent's real behavior, not just what a
judge says about stale output.

Usage (from repo root, venv active):
    python backend/scripts/replay_disambiguation.py OBS_ID [OBS_ID ...]
"""
from __future__ import annotations

import argparse
import json

import httpx
from sqlmodel import Session

from applysync.config import get_settings
from applysync.db import repository as repo
from applysync.db.init_db import get_engine
from applysync.gmail.client import GmailClient
from applysync.gmail.models import RawEmail
from applysync.llm import get_chat_model
from applysync.pipeline.state import JobApplicationEvent
from applysync.research.disambiguate import DisambiguationError, run_disambiguation
from applysync.search import get_search_client


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("observation_ids", nargs="+")
    args = parser.parse_args()

    settings = get_settings()
    model = get_chat_model(settings, model_name=settings.llm_escalation_model)
    gmail_client = GmailClient(settings)
    search_client = get_search_client(settings)

    with httpx.Client(
        base_url=settings.langfuse_host,
        auth=(settings.langfuse_public_key, settings.langfuse_secret_key),
        timeout=30.0,
    ) as lf_client, Session(get_engine(settings.db_path)) as session:
        for obs_id in args.observation_ids:
            resp = lf_client.get(f"/api/public/observations/{obs_id}")
            resp.raise_for_status()
            obs = resp.json()
            old_output = obs.get("output") or {}
            inp = obs.get("input") or {}

            email_dict = inp["email"]
            current_email = RawEmail(**email_dict)
            extracted = JobApplicationEvent(**inp["extracted"])
            candidate_ids = inp.get("candidate_ids") or []
            candidates = [c for c in (repo.get_application(session, cid) for cid in candidate_ids) if c]

            print(f"\n=== {obs_id} ===")
            print(f"  OLD verdict: {json.dumps(old_output)}")
            try:
                new_verdict = run_disambiguation(
                    current_email,
                    extracted,
                    candidates,
                    session=session,
                    gmail_client=gmail_client,
                    search_client=search_client,
                    model=model,
                )
                print(f"  NEW verdict: {new_verdict.decision} (application_id={new_verdict.matched_application_id})")
                print(f"  NEW reasoning: {new_verdict.reasoning}")
            except DisambiguationError as exc:
                print(f"  NEW verdict: FAILED OPEN - {exc}")


if __name__ == "__main__":
    main()
