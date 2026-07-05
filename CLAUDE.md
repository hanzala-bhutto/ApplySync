# ApplySync

## What this is

A self-hosted tool that reads the user's Gmail, uses an LLM pipeline to extract
job-application data (company, title, platform, status) from platform emails
(LinkedIn, Indeed, StepStone, jackandjill.ai, ...), and persists it to a local
SQLite database viewable in a small web dashboard.

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
  brittleness this project avoids. New platforms get added via
  `config/sources.yaml` (sender domains/keywords), not new parsing code.
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

1. `fetch_emails`: filtered Gmail query, excludes already-processed ids.
2. `classify_relevant`: is this a job-application email, which platform?
3. `extract_structured_data`: structured-output extraction into
   `JobApplicationEvent` (Pydantic); low-confidence/missing-field results route
   to an errors path instead of continuing.
4. `match_existing_application`: new vs. update-existing vs. duplicate;
   heuristic-first, LLM fallback only for ambiguous matches.
5. `upsert_db`: deterministic, no LLM.

Follow-up reminders are a dashboard SQL query, not a graph node.

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
- [ ] M1b: Manual extraction spike, run `scripts/gmail_probe.py` against real
      inbox after `/gmail-setup`, hand-verify 5-10 samples per platform
- [ ] M2: LangGraph pipeline + SQLite persistence + idempotency
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
