"""Run the three LLM-as-judge evaluators (docs/feasibility/langfuse-judge-prompts.md)
against existing Langfuse observations directly via the API.

Exists because the self-hosted Langfuse build running in langfuse/docker-compose.yml
does not expose the Traces-table "Actions -> Evaluate" backfill flow the hosted docs
describe (confirmed missing in the UI, not just overlooked) - see
docs/feasibility/langfuse-llm-judge.md for why manual/backfill scoring (not live) is
the deliberate design here. This script gets the same result (real BOOLEAN scores on
real observations) by doing what the evaluator worker would have done: for each
observation matching a stage's target name, call the judge model with the stage's
committed prompt and POST the verdict back as a score.

Judge model call uses model.bind_tools() with a single submit_judgment tool rather
than with_structured_output or free-text parsing - mirroring research/disambiguate.py's
proven pattern for this model family with flat scalar tool args (see CLAUDE.md's LLM
section: this model is unreliable with with_structured_output but reliable with native
tool-calling for scalar args).

Usage (from repo root, venv active, LANGFUSE_PUBLIC_KEY/SECRET_KEY + NVIDIA_API_KEY set):
    python backend/scripts/run_llm_judge_backfill.py --session-id <id> \
        [--stage relevance extraction disambiguation] [--limit N] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys

# Windows' console defaults to cp1252, which can't encode emoji that show up
# for real in judge reasoning text (a Gmail thumbs-up-reaction email was one
# of the graded observations) - crashed a real run mid-batch on this before.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

from applysync.config import get_settings
from applysync.llm import get_chat_model

_STAGES = {
    "relevance": {
        "observation_name": "scrutinize_relevance",
        "score_name": "relevance_correct",
        "prompt": """You are checking whether a job-application-tracking pipeline correctly decided
if an email is relevant (an actual application confirmation/status update) or
not (a job alert digest, newsletter, unrelated email).

Email being classified:
{input}

Pipeline's decision:
{output}

Is this decision correct? Call submit_judgment with your verdict.""",
    },
    "extraction": {
        "observation_name": "classify_and_extract",
        "score_name": "extraction_correct",
        "prompt": """You are checking whether a job-application-tracking pipeline correctly
extracted company name, job title, and status (applied/viewed/assessment/
interview/offer/rejected/other) from an application-related email.

Email:
{input}

Pipeline's extraction:
{output}

Check each field against the email content. Default to "applied" is only
correct if the email doesn't unambiguously state a later stage - a neutral
"we'll review your application" email is NOT evidence of interview/rejection.
Call submit_judgment with your verdict.""",
    },
    "disambiguation": {
        "observation_name": "disambiguate_match",
        "score_name": "disambiguation_correct",
        "needs_tool_evidence": True,
        "prompt": """You are checking whether an agent correctly decided if a new job-application
email is a status update for an EXISTING application on record, a genuinely
DIFFERENT role at the same company, or a redundant DUPLICATE.

New email and candidate applications it was compared against:
{input}

Evidence the agent actually gathered by calling its tools (get_status_history,
read_source_email) during its investigation - this is the ONLY source of truth
about the existing candidate application(s), do not assume anything about them
beyond what appears here:
{evidence}

Agent's verdict and reasoning:
{output}

A missing job title alone is not proof of a different application. Check
whether the agent's stated reasoning is actually supported by the evidence
block above - if the agent's reasoning cites a date, role, or status for an
existing application that does NOT appear in the evidence block, that is a
real hallucination and the verdict is incorrect. If the evidence block is
empty, the agent gathered no evidence at all before deciding; call that
incorrect only if the verdict was same_application or duplicate (those
require evidence per this agent's own design) - a new_application verdict
with no evidence gathered can still be correct if the new email itself gives
no reason to think it's related to the candidates. Call submit_judgment with
your verdict.""",
    },
}


@tool
def submit_judgment(correct: bool, reasoning: str) -> str:
    """Submit your final verdict. correct is True if the pipeline's decision/output
    was right, False otherwise. reasoning is a brief explanation."""
    return "recorded"


def _fetch_tool_evidence(client: httpx.Client, observation_id: str) -> str:
    """The disambiguate_match CHAIN observation's own input/output does not
    include what its get_status_history/read_source_email tool calls actually
    returned (those are sibling TOOL observations under the same parent) - a
    real gap found while reviewing the first judge pass: the judge flagged
    ~20% of verdicts as "hallucinated" reasoning it had no way to verify,
    since it never saw the tool results the agent's reasoning was grounded in."""
    response = client.get(
        "/api/public/observations", params={"parentObservationId": observation_id, "limit": 20}
    )
    response.raise_for_status()
    tool_calls = [o for o in response.json().get("data", []) if o.get("type") == "TOOL"]
    if not tool_calls:
        return "(none - the agent did not call any evidence-gathering tool before deciding)"
    parts = []
    for call in tool_calls:
        if call.get("name") == "submit_judgment" or call.get("name") == "submit_verdict":
            continue  # the final verdict call itself, not evidence
        parts.append(f"{call.get('name')}({json.dumps(call.get('input'))}) -> {call.get('output')}")
    return "\n\n".join(parts) if parts else "(none - only the final verdict tool call was made)"


def _fetch_observations(
    client: httpx.Client, session_id: str, observation_name: str, limit: int | None
) -> list[dict]:
    results: list[dict] = []
    page = 1
    while True:
        response = client.get(
            "/api/public/observations",
            params={
                "sessionId": session_id,
                "name": observation_name,
                "page": page,
                "limit": 100,
            },
        )
        response.raise_for_status()
        payload = response.json()
        results.extend(payload.get("data", []))
        if limit and len(results) >= limit:
            return results[:limit]
        meta = payload.get("meta") or {}
        if page >= meta.get("totalPages", page):
            break
        page += 1
    return results


def _fetch_already_scored_observation_ids(client: httpx.Client, score_name: str) -> set[str]:
    """A prior run of this script (or the background task getting killed
    mid-run, a real thing that happened once) may have already written some
    scores - re-judging and double-POSTing those would just waste judge calls
    and leave duplicate scores on the same observation. v3/scores paginates
    by cursor, unlike observations' page-based pagination."""
    scored: set[str] = set()
    cursor: str | None = None
    while True:
        params = {"name": score_name, "limit": 100, "fields": "subject"}
        if cursor:
            params["cursor"] = cursor
        response = client.get("/api/public/v3/scores", params=params)
        response.raise_for_status()
        payload = response.json()
        for score in payload.get("data", []):
            subject = score.get("subject") or {}
            if subject.get("kind") == "observation" and subject.get("id"):
                scored.add(subject["id"])
        cursor = (payload.get("meta") or {}).get("cursor")
        if not cursor:
            break
    return scored


def _judge_one(
    model, prompt_template: str, observation: dict, evidence: str | None = None
) -> tuple[bool, str] | None:
    format_kwargs = {
        "input": json.dumps(observation.get("input"), default=str),
        "output": json.dumps(observation.get("output"), default=str),
    }
    if evidence is not None:
        format_kwargs["evidence"] = evidence
    prompt = prompt_template.format(**format_kwargs)
    # stop_after_attempt=5 (not 3) matches the pipeline's own classify_and_extract
    # retry budget - the free tier is a shared pool, so 503 "worker limit reached"
    # happens even under our own rate cap from other users' load (see CLAUDE.md's
    # LLM section). A real batch run hit this and, uncaught, killed the whole
    # script - now caught per-observation so one bad call doesn't lose the batch.
    bound = model.bind_tools([submit_judgment]).with_retry(
        stop_after_attempt=5, wait_exponential_jitter=True
    )
    messages = [HumanMessage(content=prompt)]
    for _ in range(2):  # one nudge retry if the model answers in prose instead of calling the tool
        try:
            response = bound.invoke(messages)
        except Exception as exc:  # noqa: BLE001 - degrade this one observation, don't crash the batch
            print(f"    judge call failed: {exc}")
            return None
        tool_calls = getattr(response, "tool_calls", None) or []
        if tool_calls:
            args = tool_calls[0]["args"]
            return bool(args["correct"]), str(args.get("reasoning", ""))
        messages.append(response)
        messages.append(HumanMessage(content="Call submit_judgment with your verdict."))
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--stage", nargs="+", choices=list(_STAGES), default=list(_STAGES))
    parser.add_argument("--limit", type=int, default=None, help="cap observations per stage (for a test run)")
    parser.add_argument("--offset", type=int, default=0, help="skip the first N observations (for chunked re-runs without --skip-scored)")
    parser.add_argument("--dry-run", action="store_true", help="judge but don't POST scores")
    parser.add_argument(
        "--skip-scored",
        action="store_true",
        help="skip observations that already have a score with this stage's score_name (resume a partial run)",
    )
    args = parser.parse_args()

    settings = get_settings()
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        raise SystemExit("LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY not set - nothing to do.")
    if not settings.nvidia_api_key:
        raise SystemExit("NVIDIA_API_KEY not set - can't run the judge model.")

    model = get_chat_model(settings, model_name=settings.llm_escalation_model)

    with httpx.Client(
        base_url=settings.langfuse_host,
        auth=(settings.langfuse_public_key, settings.langfuse_secret_key),
        timeout=30.0,
    ) as client:
        for stage in args.stage:
            cfg = _STAGES[stage]
            observations = _fetch_observations(
                client, args.session_id, cfg["observation_name"], None
            )
            if args.skip_scored:
                already = _fetch_already_scored_observation_ids(client, cfg["score_name"])
                before = len(observations)
                observations = [o for o in observations if o["id"] not in already]
                print(f"  skipping {before - len(observations)} already-scored observation(s)")
            if args.offset:
                observations = observations[args.offset :]
            if args.limit:
                observations = observations[: args.limit]
            print(f"\n=== {stage} ({cfg['observation_name']}): {len(observations)} observation(s) ===")

            true_count = 0
            false_count = 0
            error_count = 0
            for obs in observations:
                evidence = _fetch_tool_evidence(client, obs["id"]) if cfg.get("needs_tool_evidence") else None
                verdict = _judge_one(model, cfg["prompt"], obs, evidence)
                if verdict is None:
                    error_count += 1
                    print(f"  {obs['id']}: judge failed to return a verdict, skipped")
                    continue
                correct, reasoning = verdict
                if correct:
                    true_count += 1
                else:
                    false_count += 1
                print(f"  {obs['id']}: {'CORRECT' if correct else 'INCORRECT'} - {reasoning[:120]}")

                if not args.dry_run:
                    resp = client.post(
                        "/api/public/scores",
                        json={
                            "traceId": obs["traceId"],
                            "observationId": obs["id"],
                            "name": cfg["score_name"],
                            "value": 1 if correct else 0,
                            "dataType": "BOOLEAN",
                            "comment": reasoning,
                        },
                    )
                    resp.raise_for_status()

            total = true_count + false_count
            accuracy = (true_count / total * 100) if total else 0.0
            print(
                f"  -> {true_count}/{total} correct ({accuracy:.1f}%), "
                f"{false_count} flagged incorrect, {error_count} judge errors"
            )


if __name__ == "__main__":
    main()
