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
[Gmail API] --(poll, filtered query)--> gmail/client.py
                                              |
                                   raw email batch
                                              v
                    LangGraph pipeline: pipeline/graph.py
   fetch_emails -> classify_and_extract -> match_existing_application
        -> upsert_db
                                              |
                                              v
                          SQLite: db/models.py + repository.py
                                              |
                                              v
                   FastAPI JSON API (web/api.py, /api/*)
                                              |
                                              v
                    React frontend (frontend/, separate dev server)

              Scheduler: APScheduler --triggers--> pipeline run every N min
              [Phase 2] LangSmith / Langfuse tracing wraps the LangGraph run
```

**Tech choices** (see plan for full reasoning, don't relitigate without new
information):
- **Frontend: React (Vite + TypeScript) + Tailwind + shadcn/ui + Framer
  Motion + dnd-kit**, calling a FastAPI JSON API. This reverses the original
  M3 choice (FastAPI + Jinja2 + HTMX, avoiding a React SPA as disproportionate
  setup cost for a single-user tool) - the original Jinja2/HTMX dashboard
  works and is not being deleted casually, but the user explicitly wants
  React specifically for its accessible component ecosystem (Radix via
  shadcn/ui - real keyboard nav/focus management, hard to hand-roll
  correctly) and smoother animation (Framer Motion), and considers that
  worth the added build/maintenance surface for this project. Both frontends
  run as separate dev servers (not unified single-command serving) per an
  explicit user choice - simpler setup over convenience.
  Migration sequenced as: (1) JSON API alongside the existing Jinja routes,
  verified with tests before touching any frontend code, (2) React app
  scaffold + read-only dashboard parity, (3) interactivity/animation, (4)
  remove the old Jinja2 templates/routes once parity is confirmed, plus an
  accessibility pass and Playwright E2E tests. Don't delete `web/app.py`'s
  Jinja routes or `web/templates/` until step 4 is actually reached and
  verified - they're the fallback/reference during migration, not dead code
  yet.
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
- [x] M3a: FastAPI dashboard core (status board, timeline, by-platform,
      reminders), server-rendered, verified against real data.
- [x] M3b: Interactive redesign. Tailwind CDN (chosen over Pico.css:
      a custom Kanban layout needs utility-class control a classless
      framework doesn't give you; CDN build is a known non-production
      tradeoff, fine for a personal local tool), SortableJS drag-and-drop
      between status columns (`PATCH /applications/{id}/status`), HTMX
      inline field editing (`PATCH /applications/{id}`), and a "reprocess
      from email" action (`POST /applications/{id}/reprocess`, refetches
      the original Gmail message by its stored id and re-runs extraction
      only, not the full graph). Found and fixed a real schema-migration
      gap along the way: making `StatusEvent.source_email_id` nullable
      doesn't apply to an already-existing local `applysync.db` file
      (`create_all` only creates missing tables, never alters existing
      ones) - there is no migration tooling yet, so a schema change means
      deleting and recreating your local db for now.
- [ ] M3c: "Connect Gmail" button using a web OAuth redirect flow (not the
      installed-app local-server flow from the M1 CLI spike), so first-run
      and any future re-consent happen inside the dashboard, not the
      terminal.
- [x] Perf + accuracy pass (post-M3, triggered by the user's real 238-application
      inbox only showing ~7-8 applications): fixed Gmail pagination (was
      silently capped at 50 emails ever), merged classify+extract into one
      LLM call, switched to nemotron-3-nano-30b-a3b with reasoning off
      (~9x faster), then found and fixed a real accuracy regression from
      that speed change (see LLM section above) before it could corrupt
      real data further. Bulk-reprocessed all 237 real applications with
      the corrected pipeline: 174 corrected, 13 deleted as false positives.
- [x] React migration 1/4: JSON API. `web/api.py` adds `/api/dashboard`,
      `/api/applications/{id}` (GET/PATCH), `/api/applications/{id}/status`
      (PATCH), `/api/applications/{id}/reprocess` (POST), reusing
      repository.py/pipeline logic unchanged. CORS matched by regex
      (`http://(localhost|127.0.0.1):\d+`), not a fixed port - Vite falls
      back to the next free port whenever another project's dev server
      already holds 5173, which happened for real during this build.
      Existing Jinja2 dashboard still works unchanged, both run side by
      side during migration.
- [x] React migration 2/4: scaffold + read-only dashboard parity.
      `frontend/`: Vite (pinned to v6 - the new default "rolldown-vite" v8
      release has a broken native binding on this machine, a real,
      reproducible build failure, not a hypothetical) + React + TypeScript
      + Tailwind v4 + TanStack Query + react-router. Dashboard/detail pages
      ported from the Jinja2 templates (avatar colors, status styling,
      filters via URL search params). TypeScript compiles clean, production
      build succeeds, backend verified against the real 230+ application
      dataset via curl. Full browser-rendered verification is the user's
      own terminal, not something run/backgrounded via Claude Code CLI (see
      "Dev server policy" below) - don't background dev servers again.
- [ ] React migration 3/4: interactivity (dnd-kit, Framer Motion, inline
      edit, reprocess, toasts). Bake in real usability-heuristic
      requirements the user surfaced (NNGroup's 10 heuristics + a 2026 UX
      principles piece): undo affordance after a drag-and-drop status
      change (not just optimistic-update-and-hope), confirmation before
      destructive actions (reprocess overwriting fields), visible
      loading/status feedback, plain-language error messages (not raw
      fetch/HTTP errors), keyboard-operable drag-and-drop (dnd-kit's actual
      selling point here, not just "prettier than SortableJS").
- [ ] React migration 4/4: remove old Jinja2 dashboard once parity
      confirmed, accessibility pass, Playwright E2E tests
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
