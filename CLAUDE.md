# ApplySync

## What this is

A self-hosted tool that reads the user's Gmail, uses an LLM pipeline to extract
job-application data (company, title, platform, status) from application
confirmation emails, and persists it to a local SQLite database viewable in a
small web dashboard.

Two purposes, both load-bearing, don't optimize away either one:
1. **Real utility**: one place to see every application and its status, instead
   of scattered platform inboxes.
2. **Learning vehicle**: the user is learning LangChain, LangChain Community,
   LangGraph, LangSmith, Langfuse, and agentic/multi-agent orchestration by
   building this. Prefer designs that make concepts legible (distinct
   nodes/agents with clear responsibilities) over the shortest path to working
   code.

Full design rationale lives in the plan this was built from:
`C:\Users\Hp\.claude\plans\i-want-to-learn-floating-mitten.md` (not part of
this repo, a local planning artifact). This file is the durable, in-repo
summary; keep it updated as milestones land instead of re-deriving context
each session.

## Hard constraints (do not violate)

- **Gmail scope is readonly.** Never request or use write/send scopes.
- **Never touch the user's manual folder structure**
  (`company_name/job+date/jobtitle_CV`). This tool tracks *alongside* that
  workflow, read-only with respect to it. Do not create, rename, or move
  folders there.
- **LLM-based extraction, not per-platform parsers.** Don't write regex/HTML
  scrapers keyed to a specific platform's email template, that's the exact
  brittleness this project avoids. New attribution vendors get added via
  `config/sources.yaml`, not new parsing code.
- **Gmail query is keyword-only, not sender-domain-restricted.** Confirmed
  against a real inbox: application confirmations come from an unenumerable
  set of senders (every ATS vendor, every company's own domain), so a domain
  allowlist misses most of them (LinkedIn Easy Apply and jackandjill.ai send
  no confirmation at all; direct/ATS confirmations come from arbitrary
  domains like `smartrecruiters.com`, `personio.de`, `ashbyhq.com`,
  `msg.join.com`, a company's own domain, etc., which cannot be enumerated in
  advance). `build_search_query` filters on `confirmation_keywords` (subject
  phrases) only. `sources.yaml`'s `platforms` list with `sender_domains` is
  used only for best-effort dashboard labeling (`guess_platform`), never for
  filtering what gets fetched.
- **Observability is phase 2.** Don't add LangSmith/Langfuse tracing or eval
  scaffolding until the core pipeline (M1 through M4 below) works end-to-end. Don't
  gold-plate this early.
- **The `mcp__claude_ai_Gmail__*` MCP tools are for this assistant's own use in
  this session only.** The shipped application must implement its own Gmail
  API OAuth flow (`credentials.json` + cached `token.json`), never wire the
  app to depend on MCP tools at runtime.

## Architecture

```
[Gmail API] --(poll, filtered query)--> gmail/client.py
                                              |
                                   raw email batch
                                              v
                    LangGraph pipeline: pipeline/graph.py
   fetch_emails -> classify_relevant -> extract_structured_data
        -> match_existing_application -> upsert_db
                                              |
                                              v
                          SQLite: db/models.py + repository.py
                                              |
                                              v
                        Web UI: FastAPI + Jinja2 + HTMX (web/app.py)

              Scheduler: APScheduler --triggers--> pipeline run every N min
              [Phase 2] LangSmith / Langfuse tracing wraps the LangGraph run
```

**Tech choices** (see plan for full reasoning, don't relitigate without new
information):
- Web UI: FastAPI + Jinja2 + HTMX. Not Streamlit (teaches nothing about
  backend/API design). Not a React SPA (disproportionate setup cost for a
  single-user local tool).
- ORM: SQLModel, shares Pydantic modeling with the LLM structured-output
  schema.
- Scheduler: APScheduler, in-process with the FastAPI app for v1.
- Idempotency: the `processed_emails` table is the business-logic guard;
  `langgraph-checkpoint-sqlite` checkpointing is crash-recovery, not a
  replacement for it.

## SQLite schema (see plan for full column list)

- `applications`: one row per job application, `UNIQUE(company_name,
  job_title, platform, applied_date)` soft-dedupe guard.
- `status_events`: history of status changes per application, links back to
  the source Gmail message id.
- `processed_emails`: idempotency backbone; every processed message id lives
  here so scheduled re-runs never reprocess it.
- `pipeline_runs`: per-run stats, powers "last synced" in the UI.

## LangGraph pipeline nodes

The compiled graph (`pipeline/graph.py::build_graph`) processes **one email
per invocation** (`process_emails` loops over the batch, calling
`graph.invoke` once per new email with `thread_id=message_id`).
`fetch_emails` is therefore NOT a graph node: it is a plain batch fetch in
`process_emails`/`run_sync`, since per-node execution here operates on a
single email. `processed_emails` (checked before invoking the graph at all)
is the idempotency guard; the `SqliteSaver` checkpointer wired into `compile()`
is crash-recovery only.

1. `classify_relevant`: is this a job-application email? Conditional edge:
   relevant -> `extract_structured_data`, irrelevant -> `mark_irrelevant`.
2. `extract_structured_data`: structured-output extraction into
   `JobApplicationEvent` (Pydantic). Conditional edge: only `company_name`
   missing routes to `mark_extraction_failed` (an error state, not a crash);
   a missing `job_title` is normalized to a fixed sentinel
   (`nodes.UNSPECIFIED_JOB_TITLE`) instead, found necessary after a real
   email (EGYM, no title in the body) got two different hallucinated
   placeholders ("Not specified" / "Unknown") on two separate runs, which
   silently created two application rows instead of deduping to one.
3. `match_existing_application`: new vs. update-existing vs. duplicate.
   Heuristic match via `repository.find_matching_application`: company+title+
   platform, normalized (lowercase, whitespace, legal suffixes like SE/GmbH/
   Inc/Ltd/AG/Co/LLC/Corp stripped) so e.g. "EGYM" and "EGYM SE" from two
   emails for the same application still match. Found necessary after a real
   pair of EGYM confirmation emails extracted with different suffixes
   created two application rows instead of one; normalization is for
   matching only, original casing is still stored/displayed.
   **Known remaining gap**: a missing job_title vs. a genuinely different
   job_title are not the same kind of mismatch, but the heuristic can't tell
   them apart (seen for real: two Nagarro applications, one with an actual
   title and one where the title just wasn't extracted, correctly did NOT
   dedupe, but it's unclear if that's actually right). Disambiguating that
   needs real judgment (date proximity, an LLM asking "same application?"),
   which is the LLM-fallback-for-ambiguous-matches idea from the original
   design, still not implemented. Don't attempt a bigger fuzzy-matching
   rewrite without a concrete case in hand; note more real examples here as
   they show up.
4. `upsert_db`: deterministic, no LLM. Always calls `mark_processed`
   regardless of new/update/duplicate_skip.
5. `mark_irrelevant` / `mark_extraction_failed`: both just call
   `mark_processed` with a different `classification` value and write no
   application/event rows, so skipped emails are never retried but the
   reason they were skipped is recorded.

Follow-up reminders are a dashboard SQL query, not a graph node.

**LLM**: `nvidia/nemotron-3-ultra-550b-a55b` via `langchain-nvidia-ai-endpoints`
(`ChatNVIDIA`), chosen for native tool-calling/structured-output fine-tuning.
Two things required after hitting them against the real API:
- **Client-side rate limiting** (`llm.py`, `InMemoryRateLimiter` at 40
  requests/min): NVIDIA's free tier caps at 40 RPM and returns a 503
  ("Worker local total request limit reached") past it; throttling
  client-side avoids burning retry/backoff time on avoidable 503s.
- **`.with_retry(stop_after_attempt=5, wait_exponential_jitter=True)`** on
  both the classify and extract model calls, for genuinely transient
  failures (the free tier is a shared pool, so 503s can still happen even
  under our own 40 RPM cap from other users' load).

## Repo layout

```
src/applysync/
  config.py
  gmail/client.py, gmail/query_builder.py
  pipeline/state.py, pipeline/nodes.py, pipeline/graph.py
  db/models.py, db/repository.py, db/init_db.py
  web/app.py, web/templates/, web/static/
  scheduler/run_scheduler.py
  cli.py                    # `applysync sync`, `applysync serve`
config/sources.yaml
eval/samples/, eval/run_eval.py   # phase 2
tests/
scripts/gmail_probe.py
```

## Milestones (update status here as they land)

- [x] M1a: Gmail OAuth client, query builder, message parsing (code done, tested)
- [x] M1b: Manual extraction spike, ran `scripts/gmail_probe.py` against the
      real inbox. Initial design (sender-domain allowlist: LinkedIn, Indeed,
      StepStone, jackandjill.ai) turned out to be the wrong approach:
      LinkedIn Easy Apply and jackandjill.ai send no confirmation emails at
      all, and direct/ATS confirmations (SmartRecruiters, Personio, Ashby,
      join.com, Teamtailor, Rippling, Workday, onlyfy.jobs, direct company
      domains) come from senders that can't be enumerated upfront. Redesigned
      to search by `confirmation_keywords` (subject phrase) only, with no
      sender-domain restriction; verified against the real inbox, 25/25
      results were genuine application confirmations with zero domain
      filtering. `sources.yaml`'s `platforms`/`sender_domains` are now
      attribution-only. Also found and fixed two real bugs: HTML-only emails
      (jackandjill.ai) extracted as empty bodies
      (no text/plain part), and StepStone's Windows-1252 charset was being
      force-decoded as UTF-8, mangling apostrophes/umlauts.
- [x] M2: LangGraph pipeline + SQLite persistence + idempotency. Built in
      three parts: M2a (SQLModel schema + repository), M2b (node factories,
      unit tested with fake models), M2c (graph wiring + checkpointing).
      Verified against the real inbox + real NVIDIA API (not just unit
      tests): correctly classified a rejection whose subject said "thank you
      for your application" (Nagarro), correctly attributed platforms via
      `sources.yaml` including a correct fallback to "other" for a direct
      company domain (EGYM). Found and fixed one real bug (see LangGraph
      pipeline nodes section: job_title placeholder hallucination causing
      duplicate application rows) and one real infra issue (free-tier rate
      limiting, see LLM section above). Idempotency (double-run = 0 new
      rows) and status-update-links-not-duplicates both verified by
      automated tests in `tests/test_graph.py`.
- [ ] M3: Web dashboard (status board, timeline, by-platform, reminders).
      Include a "Connect Gmail" button using a web OAuth redirect flow
      (not the installed-app local-server flow from the M1 CLI spike), so
      first-run and any future re-consent happen inside the dashboard,
      not the terminal.
- [ ] M4: Scheduler/automation
- [ ] M5: LangSmith/Langfuse tracing + eval set (phase 2)

## Project skills

Invoke these instead of re-deriving the same context from scratch:

- `/docs`: regenerate project documentation (Motivation, Features,
  Architecture, Data Flow, Setup, Roadmap) from current code state.
- `/concepts`: explain a LangChain/LangGraph/LangSmith/Langfuse concept as
  it's actually used in this codebase, with a file/function pointer.
- `/test`: this project's test conventions (mocking LLM calls, throwaway
  SQLite fixtures, the idempotency double-run check, the eval runner).
- `/code-review`: project-specific review checklist (idempotency,
  credential/PII handling, schema/migration safety, prompt/schema drift).
- `/gmail-setup`: one-time Gmail OAuth setup walkthrough.
