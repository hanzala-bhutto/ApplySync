# ApplySync

## What this is

A self-hosted tool that reads the user's Gmail, uses an LLM pipeline to extract
job-application data (company, title, platform, status) from application
confirmation emails, and persists it to a local SQLite database viewable in a
small web dashboard.

The goal is one place to see every application and its status, instead of
scattered platform inboxes, plus the web-research capabilities layered on top
(company research, follow-up drafting, entity resolution) that make it more than
a passive inbox reader.

**Design principle**: prefer distinct nodes/agents with clear, single
responsibilities over the shortest path to working code. This is not gold-
plating - the codebase's own history (per-node bugs like the EGYM dedupe, the
job-title placeholder hallucination, the lookback-buffer edge case) shows a
pipeline built from small, legible, independently-testable stages is far easier
to debug and extend than one monolithic call. Keep it that way.

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
  `backend/config/sources.yaml`, not new parsing code.
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
- **Observability and eval tooling landed as M5 (reliability phase), not
  before.** The eval harness and self-hosted Langfuse tracing (see M5 below)
  were deliberately built only after the core pipeline (M1-M4) worked
  end-to-end - don't add more observability/eval scaffolding ahead of an
  actual, demonstrated need for it now that this phase exists either.
- **The `mcp__claude_ai_Gmail__*` MCP tools are for this assistant's own use in
  this session only.** The shipped application must implement its own Gmail
  API OAuth flow (`credentials.json` + cached `token.json`), never wire the
  app to depend on MCP tools at runtime.
- **Don't background the backend or frontend dev servers via Claude Code's
  own tools.** The user explicitly asked for this after a long session of
  real, reproducible pain: backgrounded processes across many tool calls
  left orphaned "ghost" listeners (visible in the OS network stack, holding
  a port, but with no corresponding process - Windows-specific, seen
  repeatedly on ports 8000 and 5173), causing hours of confusion debugging
  what looked like app bugs but were actually stale processes serving old
  code. Give the user the exact commands (`applysync serve --reload` /
  `npm run dev` in `frontend/`) and let them run each in their own terminal.
  A single one-off command to check something (curl, a quick TestClient
  call, `npm run build`) is fine; a long-running dev server is not.

## Architecture

```
[Gmail API] --(poll, keyword-filtered query, concurrent fetch)--> gmail/client.py
                                              |
                                   raw email batch (fetched in process_emails,
                                   filtered by processed_emails idempotency)
                                              v
                    LangGraph pipeline: pipeline/graph.py (one email per invocation)
   scrutinize_relevance -> classify_and_extract -> match_existing_application -> upsert_db
     (any of scrutinize/classify/extract can short-circuit to a mark_* skip
      node -> END; see "LangGraph pipeline nodes" below for the exact routing)
                                              |
                                              v
                          SQLite: db/models.py + repository.py
                                              |
                                              v
                   FastAPI JSON API (web/api.py, /api/*)
                                              |
                                              v
                    React frontend (frontend/, separate dev server)

              Manual trigger: POST /api/sync -> background thread runs the pipeline once
              [Not built yet] Scheduler: OS-level scheduled task -> `applysync sync` daily
                              (in-process APScheduler ruled out, see M4)
              Self-hosted Langfuse (langfuse/, docker-compose) traces every
              node + agent tool loop of a sync; tracing is diagnostic only,
              never load-bearing (see M5 step 2)
```

**Tech choices** (see plan for full reasoning, don't relitigate without new
information):
- **Frontend: React (Vite + TypeScript) + Tailwind + Framer Motion + dnd-kit**,
  calling a FastAPI JSON API (`web/api.py`). This reverses the original M3
  choice (FastAPI + Jinja2 + HTMX, avoiding a React SPA as disproportionate
  setup cost for a single-user tool) - the user explicitly wants React for
  its accessible component ecosystem and smoother animation, and considers
  that worth the added build/maintenance surface for this project. Both
  frontends run as separate dev servers (not unified single-command
  serving) per an explicit user choice - simpler setup over convenience.
  The old Jinja2 dashboard (`web/templates/`, HTML-rendering routes) is
  **gone**, removed once the read-only React dashboard reached parity -
  `web/app.py` is now just CORS + API registration, nothing else. This
  happened before interactivity (drag-and-drop, edit, reprocess) was ported
  to React, an explicit user call to cut over early rather than run both
  UIs until full parity. Until React migration 4/4 lands, the dashboard is
  browse/filter/navigate only; status corrections and reprocessing exist
  in the API but nothing in the frontend calls them yet.
- **API responses use explicit Pydantic response models** (not raw dicts),
  specifically so FastAPI's auto-generated Swagger UI (`/docs`) and
  `/openapi.json` produce a real, useful schema - this was an explicit ask,
  not just a nice-to-have.
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

1. `classify_and_extract`: ONE structured-output call doing both
   classification and extraction (`ClassifyAndExtractResult`), not two
   separate calls. Halving the LLM round-trips per email mattered in
   practice, see the LLM section below. Conditional edge: `extracted`
   present -> `match_existing_application`; classification came back
   irrelevant -> `mark_irrelevant`; otherwise (missing company_name, or the
   call itself failed/returned None) -> `mark_extraction_failed`. A missing
   `job_title`, or known placeholder text the model still occasionally
   emits despite being told not to ("not specified", "unknown", "n/a", ...),
   normalizes to a fixed sentinel (`nodes.UNSPECIFIED_JOB_TITLE`) instead of
   erroring, since a genuinely missing title happens on real ATS emails and
   inconsistent placeholders used to silently create duplicate application
   rows instead of deduping to one.
2. `match_existing_application`: new vs. update-existing vs. duplicate.
   Heuristic-first via `repository.find_matching_application`: company+title+
   platform, normalized (lowercase, whitespace, legal suffixes like SE/GmbH/
   Inc/Ltd/AG/Co/LLC/Corp stripped) so e.g. "EGYM" and "EGYM SE" from two
   emails for the same application still match. Found necessary after a real
   pair of EGYM confirmation emails extracted with different suffixes
   created two application rows instead of one; normalization is for
   matching only, original casing is still stored/displayed. An exact-title
   hit resolves immediately (`update_existing`); no candidate at all is
   `new_application`. **The former missing-title-vs-different-title gap** (a
   missing job_title vs. a genuinely different one look identical to the
   heuristic - the real Nagarro pair) **is now handled by the disambiguation
   agent**: when the exact-title match misses but same-company+platform
   candidates exist (`repo.find_candidate_applications`), the node emits
   `candidate_ids` and leaves `match` unset, and a conditional edge routes to
   `disambiguate_match` (the LLM tool-loop agent, see the milestone entry for
   Entity/duplicate resolution and `research/disambiguate.py`) instead of
   blindly creating a new row. The agent fails open to `new_application`, so a
   degraded run or missing clients never blocks the pipeline. Before the model
   runs at all, a deterministic **requisition-ID short-circuit**
   (`_extract_req_ids`/`_REQ_ID_RE`, 5-8 digit ATS req numbers) resolves the
   case in Python when the new email and exactly one candidate share a req ID -
   an exact same-posting signal the model got wrong even with the ID in front
   of it (see the cross-provider milestone entry below).
3. `upsert_db`: deterministic, no LLM. Always calls `mark_processed`
   regardless of new/update/duplicate_skip.
4. `mark_irrelevant` / `mark_extraction_failed`: both just call
   `mark_processed` with a different `classification` value and write no
   application/event rows, so skipped emails are never retried but the
   reason they were skipped is recorded.

Follow-up reminders are a dashboard SQL query, not a graph node.

**LLM**: `nvidia/nemotron-3-nano-30b-a3b` via `langchain-nvidia-ai-endpoints`
(`ChatNVIDIA`), reasoning/"thinking" disabled via
`model_kwargs={"chat_template_kwargs": {"thinking": False}}`, `temperature=0`.
Was `nemotron-3-ultra-550b-a55b` (2 calls/email, ~7-7.6s/call baseline); the
current combo (1 call/email, ~0.81s measured with reasoning off) is roughly a
9x speedup total, needed to make a 200+-application real sync complete in
minutes instead of nearly an hour. That speed change cost real accuracy at
first (see below) - don't swap models or disable reasoning again without
re-running the accuracy check this section describes.
- **Client-side rate limiting** (`llm.py`, `InMemoryRateLimiter` at 40
  requests/min): NVIDIA's free tier caps at 40 RPM and returns a 503
  ("Worker local total request limit reached") past it; throttling
  client-side avoids burning retry/backoff time on avoidable 503s. Once
  per-call latency is fast enough, this 40 RPM cap becomes the real floor
  for how fast a large sync can go (`N emails / 40 * 60` seconds minimum),
  not model speed - confirmed against a real 430-email backfill (~14.8 min
  actual vs. ~10.75 min theoretical floor at 40 RPM).
- **`.with_retry(stop_after_attempt=5, wait_exponential_jitter=True)`** on
  the classify+extract call, for genuinely transient failures (the free tier
  is a shared pool, so 503s can still happen even under our own 40 RPM cap
  from other users' load).
- **Optional Groq hybrid for the disambiguation agent only** (`llm.py`:
  `get_agent_model` -> `ChatGroq`, gated behind `groq_api_key`/`groq_agent_model`
  in `config.py`). When configured, only `disambiguate_match` runs on Groq
  (its own account, its own `_limiter(30)` rate budget, ~8x lower latency than
  the NVIDIA agent path), composed with `.with_fallbacks([nvidia_escalation])`
  so a Groq 429/outage transparently switches back to NVIDIA. Extraction and
  scrutiny always stay on NVIDIA. Inactive by default (both env vars unset ->
  agent uses the NVIDIA escalation model exactly as before). The shared
  `InMemoryRateLimiter` is now keyed by RPM (`_limiter(rpm)`), so NVIDIA's 40
  and Groq's 30 each get one process-wide instance rather than one per model.

**Extraction accuracy is fragile to prompt/model changes, verify against
real emails before trusting a change.** Switching to the faster model above
initially produced real, serious errors caught by re-running 5 known real
emails through the pipeline before touching production data: hallucinated
"interview"/"offer"/"rejected" from neutral "we'll review and get back to
you" language (temperature wasn't pinned, so results also weren't even
reproducible run to run), two German-language "your draft application is
incomplete" reminders wrongly tracked as real applications, "online
assessment" confused with "interview", and company_name extraction
degrading once the prompt got longer/more detailed. All fixed in the current
prompt (`nodes._CLASSIFY_AND_EXTRACT_PROMPT`): `temperature=0`, an explicit
"default to applied unless the email unambiguously states otherwise" rule, a
new `assessment` status distinct from `interview`, bilingual
incomplete-application phrases, an explicit instruction to ignore "similar
jobs"/"you might also like" recommendation sections some ATS emails append
(these list unrelated companies that were leaking into company_name), and
defensive normalization of placeholder text the model still sometimes emits
despite being told not to. A bulk reprocess of all 237 real applications
with the corrected pipeline changed 174 of them (mostly false "rejected" ->
"applied") and deleted 13 false positives - this was systemic, not a rare
edge case. One known remaining gap: an email whose real content never states
the employer at all (info genuinely isn't there, not a model failure) can
still get a wrong company_name from surrounding noise; low frequency, not
chased further without more concrete real examples.

## Repo layout

```
backend/
  applysync/
    config.py
    gmail/client.py, gmail/query_builder.py
    pipeline/state.py, pipeline/nodes.py, pipeline/graph.py
    db/models.py, db/repository.py, db/init_db.py
    search/client.py          # SearXNG web-search client (foundation for research features)
    web/app.py, web/api.py
    scheduler/run_scheduler.py
    cli.py                    # `applysync sync`, `applysync serve`, `applysync search`
  config/sources.yaml
  scripts/gmail_probe.py
frontend/                     # React (Vite + TypeScript) dashboard, separate dev server
searxng/                      # self-hosted SearXNG (docker-compose.yml + settings.yml)
eval/samples/, eval/run_eval.py   # phase 2
tests/
```

## Web search (self-hosted, keyless)

Web-research features (planned: company-research card, follow-up "should I chase
this + warm draft", duplicate/entity resolution, company-alias canonicalization,
interview-prep dossier) get live results from a **self-hosted SearXNG** instance,
never a paid API or external account - a deliberate choice to keep the tool
local-first and keyless, matching the rest of the design. `searxng/` holds a
single-service `docker-compose.yml` (no Redis: the bot-detection limiter is
disabled in `settings.yml`, which is what would otherwise require it) exposing
SearXNG's JSON API on `http://localhost:8888`. `backend/applysync/search/client.py`
is a thin httpx client (`SearxngClient.search()` returns parsed `SearchResult`s,
raises `SearxngError` on failure so a real outage is never silently confused with
"no results"); `get_search_client(settings)` follows the same DI pattern as
`get_gmail_client`/`get_llm_model` so tests inject a fake. `applysync search
"<query>"` is a CLI smoke test. **Hard rule for the features built on top of
this**: web-sourced data must stay visually and schema-separated from
email-extracted facts - never let "the internet suggested this" get mistaken for
"the company told me this" (this project's oldest data-integrity sensitivity).
The service itself is a required-running dependency for those features (like the
dev servers, the user starts it in their own terminal: `docker compose up -d` in
`searxng/`); the SearXNG container is detached/Docker-managed, so it is not
subject to the no-backgrounding-dev-servers constraint the way the app's own
`serve`/`npm run dev` are.

## Milestones

Full narrative history - what shipped, the bugs found along the way, and the
reasoning behind each decision - lives in `docs/CHANGELOG.md`. This section is
the compact index plus everything not yet built. The durable rules those
milestones produced live in their own sections above (Hard constraints,
LangGraph pipeline nodes, LLM, Web search); update those, not just this list,
when a rule changes.

### Shipped (see `docs/CHANGELOG.md` for the detail and bug history behind each)

- **M1** Gmail ingestion: keyword-only query (no sender allowlist, verified
  25/25 against the real inbox), message parsing, HTML-body + charset fixes.
- **M2** LangGraph pipeline + SQLite persistence + idempotency (double-run = 0
  new rows), verified against the real inbox + NVIDIA API.
- **M3** Dashboard: FastAPI core (a) -> interactive HTMX/SortableJS (b) ->
  in-dashboard Gmail OAuth connect flow (c).
- **Perf + accuracy pass**: fixed the silent 50-email pagination cap, merged
  classify+extract into one call, switched to nano + reasoning-off (~9x), then
  fixed the accuracy regression that speed change caused before it corrupted
  data; bulk-reprocessed 237 apps (174 corrected, 13 deleted).
- **React migration** (Jinja2 -> React SPA): JSON API -> read-only parity ->
  Jinja2 removed -> full interactivity (dnd-kit, Framer Motion, inline edit,
  reprocess, toasts, keyboard-operable DnD) -> Playwright E2E + axe a11y ->
  polish pass (dark mode, reminders pagination, `/analytics` split).
- **Source email verification**: "View email" toggle per timeline row, live
  read-through from Gmail (nothing new stored).
- **`declined` status**: manual-only; deliberately excluded from the LLM output
  schema (declining is the user's action, never stated in an inbound email).
- **Manual "Sync Now"** (M4 precursor): background-thread `POST /api/sync`,
  concurrency lock, status polling; fixed the zero-result-run bookmark-advance
  bug via `SYNC_LOOKBACK_BUFFER_DAYS`.
- **Pipeline redesign** (#17-21): broadened keywords, concurrent Gmail fetch,
  the `scrutinize_relevance` entry node, `PipelineRun` progress fields, `/sync`
  staged-progress page.
- **Full Audit**: re-runs extraction over all history into `ReviewSuggestion`s
  (never auto-writes), reviewed on `/review`.
- **Web research**: SearXNG foundation -> company research card ->
  entity/duplicate-resolution agent (+ LLM-judge date-arithmetic fix) -> fuzzy
  company matching -> cross-provider Groq agent + req-ID short-circuit (PR #103).
- **M5 reliability push**: (1) eval harness + gold dataset, (2) self-hosted
  Langfuse + per-stage LLM-judge, (3) confidence-routed merges, (4) tiered
  escalation models, (5) shared NVIDIA rate limiter, (6) LLMOps CI automation.
- **Accuracy/correctness fixes**: status-ordering + job-title extraction (#67),
  relevance-classification (#72/#75, 79.8% -> 98.2%), real-time flow viz + Stop
  button (#89).

### Not built yet

**Web-research features** all build on the shipped foundation and reuse its
patterns, so a new session can pick any of them up cold. **Shared, already in
place**: SearXNG (`searxng/`, must be running - `docker compose up -d`), the
search client (`backend/applysync/search/client.py`, `get_search_client` DI),
and the grounded-synthesis pattern (`backend/applysync/research/company.py`).
**Two load-bearing constraints that apply to every one of these** (learned the
hard way, do not relitigate): (1) for **structured output**, this model needs
`PydanticOutputParser` over plain-text output with **flat scalar-only** schemas -
`with_structured_output` returns empty once a schema has any list field. **But
native tool-calling (`bind_tools`) IS reliable for scalar-arg tools** (verified
live while building entity resolution), so an agent can bind tools and end on a
terminal "submit" tool call rather than parsing plain text - see
`research/disambiguate.py`. Keep tool args scalar; don't put list-typed args on
a tool. (2) web-sourced data stays in its own table/response model/card, never
merged into `Application`. Ordered by dependency:

- [ ] **Company-alias canonicalization.** Resolve a company's official name +
      known aliases via search, store a mapping (new `canonical_name`/alias
      table + `repo` apply helpers). Apply at match time and as a one-off batch
      cleanup over existing rows. Feeds entity-resolution and the research-card
      cache key (dedupes "Meta"/"Facebook"/"Meta Platforms").
- [ ] **Follow-up "should I chase + warm draft".** On-demand button on the
      detail / `/reminders` pages. Small agent: search the company's recent news
      -> classify health (active / frozen / dead) -> if active, draft a warm
      follow-up email that references something current; if frozen/dead, return
      an advisory instead of a draft. New `POST
      /api/applications/{id}/follow-up-draft`. **Gmail stays readonly** - the
      draft is shown for the user to copy, never sent. Reuse `research/` + the
      grounded-parser pattern.
- [ ] **Interview-prep dossier.** On status -> `interview` (or on-demand), run
      several searches and synthesize a structured dossier (recent company news,
      interview format for the role, common questions), cached in a new
      `InterviewDossier` table + endpoint + a detail-page card. Same
      grounded-parser pattern.
- [ ] **Review-suggestion triage (full audit).** Add a confidence step to
      `pipeline/full_audit.py` that can search to verify a suggestion's
      company/domain, auto-accept high-confidence ones, and surface only the
      genuinely ambiguous. New `confidence` field on `ReviewSuggestion`. Directly
      targets the false-positive-flood pain (the "528 suggestions" commit).

- [ ] **M4: Scheduler/automation** - explicitly NOT the same as the manual "Sync
      Now" button above: an in-process APScheduler tied to the FastAPI app (the
      original plan) only ticks while `applysync serve` happens to be running,
      which doesn't fit how this tool is actually used (dashboard opened
      occasionally, not a persistent service) - a "daily sync" would silently not
      happen most days. Also ruled out: Claude Code's own cloud scheduling
      (`/schedule`, `CronCreate`) - those run in a cloud sandbox with no access to
      the local `.secrets/token.json`, local SQLite file, or local venv, and
      shipping credentials off-machine to make that work would contradict the
      self-hosted/local design. Agreed direction: an OS-level scheduled task
      (Windows Task Scheduler) running `applysync sync` once a day, independent of
      whether the dashboard/API server is open.

## Feature workflow

Before implementing any feature, write its feasibility report first
(`docs/feasibility/<slug>.md`, via `/feasibility`) - a short
Motivation/Problem/Solution/Changes/Benefits note on why the feature earns its
place. Keep it short (one line per heading); it captures the *why*, not the
design (which lives in the plan file and PR).

## Project skills

Invoke these instead of re-deriving the same context from scratch:

- `/feasibility`: write the short per-feature feasibility report
  (Motivation/Problem/Solution/Changes/Benefits) into `docs/feasibility/`.
- `/docs`: regenerate project documentation (Motivation, Features,
  Architecture, Data Flow, Setup, Roadmap) from current code state.
- `/concepts`: explain a LangChain/LangGraph/LangSmith/Langfuse concept as
  it's actually used in this codebase, with a file/function pointer.
- `/test`: this project's test conventions (mocking LLM calls, throwaway
  SQLite fixtures, the idempotency double-run check, the eval runner).
- `/code-review`: project-specific review checklist (idempotency,
  credential/PII handling, schema/migration safety, prompt/schema drift).
- `/gmail-setup`: one-time Gmail OAuth setup walkthrough.
