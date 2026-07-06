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
- [x] M3c: "Connect Gmail" button using a web OAuth redirect flow, so
      first-run and any future re-consent happen inside the dashboard, not
      the terminal. `web/gmail_oauth.py`: `GET /api/gmail/status` (token
      presence/validity check), `GET /api/gmail/connect` (builds a
      `google_auth_oauthlib.flow.Flow` and redirects to Google's consent
      screen), `GET /api/gmail/callback` (exchanges the code, writes
      `token.json`, redirects back to wherever the user started from via a
      `return_to` param round-tripped through OAuth `state`). Reused the
      existing Desktop-app `credentials.json` unchanged - Google's loopback
      redirect rules (RFC 8252) accept any `http://localhost`/`127.0.0.1`
      redirect URI for that client type, the same mechanism
      `InstalledAppFlow.run_local_server(port=0)` already relied on, so no
      separate "Web application" OAuth client was needed. Frontend:
      `GmailConnectionBanner` in `Layout.tsx` shows a "Connect Gmail" banner
      (real `<a>` navigation, not a fetch, since it has to walk through
      Google's own pages) whenever disconnected, and handles the
      `?gmail=connected`/`?gmail=error` redirect back with a toast + URL
      cleanup. `/gmail-setup` skill updated to document both paths (CLI
      first-run still works for `scripts/gmail_probe.py`).

      Three real bugs found testing the actual flow (not caught by the
      mocked backend tests, since those never exercise a real token
      exchange or a real cross-origin redirect):
      (1) `Settings.gmail_client_secrets_path`/`gmail_token_path`/`db_path`
      were relative paths resolved against whatever directory the process
      happened to be started from, not the repo root - harmless for the CLI
      (always run from repo root by convention) but `/api/gmail/connect`
      returned 500 "No Gmail client secrets file found" because the running
      server's cwd wasn't the repo root even though the file existed there.
      Fixed with a `field_validator` in `config.py` that resolves relative
      paths against `PROJECT_ROOT`. (2) `invalid_grant: Missing code
      verifier` from Google on the callback - `google-auth-oauthlib`
      defaults `autogenerate_code_verifier=True`, so the `/connect` route's
      `Flow` instance generates a PKCE `code_verifier` and sends its
      `code_challenge` to Google, but `/callback` built a *separate* `Flow`
      instance (different HTTP request, no shared state) with no verifier,
      so `fetch_token()` sent none. Fixed by storing `flow.code_verifier`
      alongside `return_to` in `_pending_states` (keyed by OAuth `state`)
      and passing it into the callback's `Flow.from_client_secrets_file(...,
      code_verifier=...)`. (3) `GmailConnectionBanner` built `return_to` as
      just `window.location.pathname + search` (e.g. `/`), a relative path -
      when the backend's callback issued `RedirectResponse(return_to +
      "?gmail=connected")`, the browser resolved that relative URL against
      the BACKEND's own origin (it's the origin the redirect response came
      from), not the frontend's, landing on
      `http://127.0.0.1:8001/?gmail=connected` (404, no such backend route)
      instead of back on the Vite dev server. Fixed by including
      `window.location.origin` in `return_to`, so it's always an absolute
      URL; the Playwright test for the banner now asserts `return_to` starts
      with `http(s)://` to catch this class of regression.
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
- [x] Removed the old Jinja2 dashboard (`web/templates/`, the HTML-rendering
      routes in `web/app.py`) ahead of full React interactivity parity -
      the user explicitly chose to cut over once the read-only React
      dashboard worked, rather than wait for drag-and-drop/edit/reprocess
      to be ported first. `web/app.py` is now just CORS + API registration;
      all response shapes get explicit Pydantic response models
      (`web/api.py`: `DashboardResponse`, `ApplicationDetailResponse`,
      `PlatformBreakdownRow`, `FilterOptionsResponse`) so FastAPI's
      auto-generated Swagger UI (`/docs`) and OpenAPI schema
      (`/openapi.json`) are actually useful, not just raw untyped dicts.
      `python-multipart`/`jinja2` dropped from dependencies (no longer used
      now that nothing renders HTML server-side or parses form bodies).
- [x] React migration 4/4a: interactivity + animation.
      - Drag-and-drop status correction: `@dnd-kit/core` (not `@dnd-kit/sortable`
        - we need cross-column moves, not in-column reordering).
        `PointerSensor` with `activationConstraint: {distance: 8}` so a card
        is still a normal clickable `<button>` (small movements are clicks,
        not drags). Optimistic update via TanStack Query's
        `onMutate`/`onError` (instant board update, rollback on failure) plus
        a success toast with an **Undo** action (re-mutates back to the old
        status) - drag-and-drop must never be a one-way door, per the
        NNGroup "user control and freedom" heuristic.
      - **Keyboard drag conflict, found and fixed**: dnd-kit's default
        `KeyboardCodes` bind *both* Space and Enter to start/end a drag. Since
        cards are real `<button>`s (Enter = native click = open detail page),
        leaving Enter bound to drag-start would fight the button's own
        keyboard behavior. Fixed by passing
        `keyboardCodes: { start: ['Space'], cancel: ['Escape'], end: ['Space'] }`
        to `useSensor(KeyboardSensor, ...)` - Enter opens, Space grabs/drops.
      - Base `@dnd-kit/core`'s keyboard coordinate getter moves the dragged
        item by fixed pixel deltas per arrow press, not "snap to nearest
        column" - reaching a distant column by keyboard may take several
        presses. Accepted as a known limitation rather than writing a custom
        coordinate getter; the `<select>` on the detail page (see below) is
        the fully robust keyboard/screen-reader path for the same action,
        per the accessibility principle that drag-and-drop should never be
        the *only* way to do something.
      - Detail page: inline edit form (toggle, not always visible), a plain
        `<select>` for status (the robust non-drag path mentioned above),
        and a reprocess button gated behind `ConfirmDialog` (native
        `<dialog>` element - free focus trapping/ESC-to-close/correct
        screen-reader dialog semantics, not hand-rolled ARIA).
      - `lib/toast.tsx`: a small `aria-live="polite"` toast system (own
        code, not a dependency) supporting an optional action button, used
        for every mutation's success/error feedback - "visibility of system
        status" and "help users recognize/diagnose/recover from errors"
        heuristics, not just decoration.
      - Framer Motion's `layout` prop on each card animates the position
        shift when a card moves between columns.
      - TypeScript compiles clean, production build succeeds. Not yet
        verified in a live browser this session - see the constraint above
        about not running dev servers via Claude Code's own tools; verified
        by the user in their own already-running terminals instead.
- [x] React migration 4/4b: Playwright E2E tests + accessibility audit.
      `frontend/e2e/` (`@playwright/test` + `@axe-core/playwright`, config
      at `frontend/playwright.config.ts`): every test mocks `/api/*` via
      `page.route` (`e2e/fixtures.ts`) instead of hitting the real FastAPI
      backend or a real Gmail-derived database, so the suite is
      self-contained and reproducible. Playwright's `webServer` builds and
      runs `vite preview` for the test run only (`npm run test:e2e`), then
      tears it down automatically - not a persistent dev server, so this
      doesn't conflict with the no-backgrounding-dev-servers constraint
      above. 12 tests across three files: dashboard rendering/filtering
      (including a direct regression test for the keepPreviousData fix,
      delaying the mocked response to assert the old board stays visible),
      application detail (status badge/select decoupling, edit form,
      reprocess confirm-dialog centering and cancel path), and keyboard
      operability (Space drags, Enter opens - the dnd-kit KeyboardCodes
      fix). `webServer.url` and `baseURL` had to use `http://localhost:4173`
      rather than `127.0.0.1:4173` - the latter didn't resolve fast enough
      on this machine and Playwright's server-ready check timed out.
      Axe found real WCAG 2 AA color-contrast failures (not false
      positives): several `text-slate-400`-on-light-background instances
      (`Layout.tsx` header subtitle, the drag-hint span and platform-total
      spans and empty-column placeholder in `Dashboard.tsx`, the detail
      page's `dt` labels and table `thead` in `ApplicationDetail.tsx`) fell
      below the required 4.5:1 ratio - all had the class order backwards
      (`text-slate-400 dark:text-slate-500`, meaning dark mode got the
      *lighter* shade and light mode got the one that needed to be darker).
      Fixed by swapping to `text-slate-500 dark:text-slate-400` (and
      `text-slate-300 dark:text-slate-600` -> `text-slate-500
      dark:text-slate-500` for the empty-column placeholder). Both
      `dashboard-has-no-detectable-accessibility-violations` and its detail-
      page equivalent pass clean after the fix. Run with `npm run test:e2e`
      in `frontend/`. Known remaining gap: axe-core catches contrast/ARIA/
      semantic issues but is not a substitute for an actual screen-reader
      pass (NVDA/VoiceOver) on the live app - not done yet, not chased
      further without a concrete reported issue.
- [x] Frontend polish pass (user-reported, post-4/4b): fixed the leftover
      Vite scaffold favicon/title (`frontend/index.html` still said
      "frontend", `public/favicon.svg` was still the default Vite logo -
      replaced with an ApplySync-branded icon and title). Lightened dark
      mode: the whole neutral/status color scale was one step darker than
      needed (page bg `slate-950`, cards `slate-900`, borders `slate-800`),
      shifted every dark-mode shade up one step across `Layout.tsx`,
      `Dashboard.tsx`, `ApplicationDetail.tsx`, `ConfirmDialog.tsx`,
      `status.ts`, `toast.tsx` - light mode untouched. Fixed follow-up
      reminders not scaling: `repository.stale_applications` had no limit or
      ordering and the dashboard rendered every stale application in one
      unbounded grid, which would not hold up at 1000+ rows. Dashboard now
      shows a bounded preview (`REMINDERS_PREVIEW_SIZE = 6`, oldest-first)
      with a "View all N" link; a new dedicated `/reminders` page + `GET
      /api/reminders` endpoint does real DB-level pagination
      (`repository.stale_applications_page`/`stale_applications_count`,
      `offset`/`limit` in SQL, not in-memory slicing) so it stays cheap
      regardless of how many rows are stale. Split the platform
      response-rate breakdown out of the dashboard into its own `/analytics`
      page for separation of concerns, added a nav bar to `Layout.tsx`
      (Dashboard / Follow-Up / Analytics). Renamed vague headings for
      clarity: "Pipeline" -> "Application Pipeline", "Follow-up reminders"
      -> "Needs Follow-Up", "By platform" -> "Response Rate by Platform".
- [x] Source email verification (user-reported follow-up to the polish
      pass): the application detail page had no way to see the email an
      extraction actually came from, only the LLM's output - no direct way
      for a human to verify the pipeline got it right. Added
      `GmailClient.get_message(message_id)` (refactored out of
      `reprocess_application`'s inline fetch, now shared) and `GET
      /api/status-events/{event_id}/email` (looks up the event's
      `source_email_id`, live-fetches from Gmail, returns
      subject/sender/date/body - nothing new stored in the DB, this is a
      read-through, same pattern as reprocess). Each Timeline row on the
      detail page got a "View email" toggle (only shown when the event has
      a `source_email_id`; manual corrections show "manual" instead) that
      expands an inline panel: truncated to 500 chars by default with a
      "Show full email" toggle, so a human can check the extraction against
      the real source without leaving the page.
- [x] Added a `declined` status (user-reported, e.g. Tekscend Photomask
      Germany GmbH - an offer/interview the user turned down themselves,
      distinct from `rejected` which means the company said no). Manual-only
      by design: added to `repo.STATUS_ORDER` (backend Kanban column order)
      and `STATUS_STYLES` (frontend, orange to stay visually distinct from
      `rejected`'s rose and `other`'s amber), but deliberately NOT added to
      the LLM's status `Literal` in `pipeline/state.py` - declining is the
      user's own action, never something stated in an inbound email, so the
      classifier should never be able to produce it. Set via the status
      dropdown or drag-and-drop like any other manual correction
      (`set_manual_status` already accepted arbitrary strings, no backend
      validation to loosen).
- [x] Manual "Sync Now" button (M4 precursor - the user chose this over
      automatic scheduling for now; see M4 below for why they're not the
      same thing). `web/sync.py`: `POST /api/sync` starts `run_sync` in a
      background `threading.Thread` (not `BackgroundTasks` - a full sync can
      take minutes at the 40 RPM rate-limit floor, and this needs to survive
      being kicked off from a request that returns immediately) and returns
      202; a module-level lock + dict (`_state`) rejects a second concurrent
      sync with 409 rather than queuing or double-running one - fine for a
      single-process, single-user tool, no task queue needed. `GET
      /api/sync/status` reports `in_progress`/`last_error`/`latest_run` (via
      new `repo.get_latest_pipeline_run`, regardless of finished state, so
      the frontend can show a run still in progress). `run_sync` is
      dependency-injected via `get_run_sync` (same pattern as
      `get_gmail_client`/`get_llm_model`) specifically so tests can swap in
      a fake instead of hitting real Gmail/LLM calls from a background
      thread. Frontend: `SyncButton` in `Layout.tsx` shows last-synced time,
      polls `/api/sync/status` every 1.5s only while `in_progress` (not
      constantly), and toasts the outcome (stats on success, the error
      message on failure) the moment it flips back to not-in-progress.

      Also fixed a UX/accessibility inconsistency found while reviewing this
      addition against the project's established mutation-feedback rules:
      the sync failure toast showed the raw backend exception text instead
      of a plain-language message (now generic, matching every other
      mutation's error toast), and every real `<button>` app-wide was
      missing `cursor-pointer` (Tailwind v4's preflight doesn't add it, so
      native buttons show the plain arrow cursor, not a hand) - fixed
      everywhere, not just the new button, for consistency.

      Two more real bugs found by actually clicking "Sync Now" against the
      real inbox (not caught by mocked tests, since those never exercise a
      real Gmail query or real keyword coverage): (1) a real rejection email
      ("Your application at dexter health") wasn't fetched at all - the
      Gmail search is subject-only, and its subject didn't match any
      `confirmation_keywords` phrase (closest was `"your application for"`,
      but this one said "at"). Added `"your application at"` to
      `config/sources.yaml`. (2) Fixing (1) alone wasn't enough: the two
      zero-result manual syncs run while testing this feature still
      completed "successfully" (0 emails found, but `finished_at` got set),
      which advanced `last_successful_run_started_at` to that day. Since
      Gmail's `after:` filter is date-only, the next sync would have used
      `after:` today's date, permanently excluding the July 5 email even
      after the keyword fix - a run that finds nothing still moves the
      incremental bookmark forward, and once a date is passed, anything
      before it is unreachable regardless of keyword coverage. Fixed by
      adding `SYNC_LOOKBACK_BUFFER_DAYS = 3` in `graph.py`, subtracted from
      the last run's date on top of the existing same-day overlap - cheap
      (the `processed_emails` idempotency table already dedupes anything
      re-fetched in the wider window), and closes this whole class of edge
      case rather than just the one instance.
- [x] Pipeline redesign (GitHub issues #17-21, real PR-per-issue workflow -
      first time this project used pushed branches + `gh pr merge` end to
      end instead of local-only merges): broadened Gmail keyword filter,
      concurrent Gmail fetch, a new `scrutinize_relevance` LangGraph node,
      `PipelineRun` incremental progress fields, and a dedicated `/sync`
      staged-progress page.
      - **#17**: `config/sources.yaml`'s `confirmation_keywords` gained
        single-word matches (`applied`, `interview`, `rejected`, `offer`,
        etc.) alongside the existing exact phrases, so emails whose subject
        doesn't match a known phrase (a real rejection email from "dexter
        health" was missed entirely before this) still get fetched.
      - **#18**: `GmailClient.fetch_messages`'s per-message body fetch is
        now a 10-worker `ThreadPoolExecutor` instead of sequential. Each
        worker thread builds its own Gmail API service instance rather than
        sharing one, since googleapiclient's httplib2 transport isn't
        documented as thread-safe.
      - **#19**: broadening the keyword filter alone would let more
        job-alert digests reach the LLM-rate-limited `classify_and_extract`
        call, so a new `scrutinize_relevance` node is now the graph's entry
        point - a hybrid heuristic (instant reject on digest markers,
        instant pass on the original narrow phrases) + one cheap
        `RelevanceOnlyResult` LLM call only for genuinely ambiguous
        subjects. Fails open (pass) on an LLM error. Rejected emails are
        marked processed with `classification="scrutiny_rejected"`, reusing
        the existing skip-node pattern.
      - **#20**: `PipelineRun` gained `emails_total`/`emails_scrutinized`/
        `emails_extracted`/`emails_written`/`updated_at`, added to an
        existing `applysync.db` via a new additive `ALTER TABLE` migration
        pass in `init_db` (this project still has no real migration tool).
        `process_emails` switched from `compiled.invoke` to
        `compiled.stream(stream_mode="updates")` so progress is observable
        node-by-node, not just once a run finishes.
      - **#21**: new `/sync` page (`frontend/src/pages/Sync.tsx`) shows a
        4-stage progress view (Ingestion/Scrutiny/Extraction/Classification-
        DB-Write) plus a recent-run history table, reusing `SyncButton` for
        the trigger so it shares the header widget's react-query cache key.
        `GET /api/sync/status` gained an optional `history` field (no new
        endpoint).
      - **Known remaining gap, not chased further yet**: none of this has
        been verified against the real inbox. The reject-marker word list
        and the ambiguous-case prompt wording are expected to need at least
        one iteration once a real sync surfaces actual false positives/
        negatives - this project's own history (the EGYM dedupe bug, the
        pagination cap bug, the lookback-buffer bug) shows this class of
        bug only ever surfaces against a real inbox, never in unit tests
        alone. Do this before trusting the broadened filter on real data.
- [ ] M4: Scheduler/automation - explicitly NOT the same as the manual
      button above: the user pointed out that an in-process APScheduler tied
      to the FastAPI app (the original plan) only ticks while `applysync
      serve` happens to be running, which doesn't fit how this tool is
      actually used (dashboard opened occasionally, not a persistent
      service) - a "daily sync" would silently not happen most days. Also
      ruled out: Claude Code's own cloud scheduling (`/schedule`,
      `CronCreate`) - those run in a cloud sandbox with no access to the
      local `.secrets/token.json`, local SQLite file, or local venv, and
      shipping credentials off-machine to make that work would contradict
      the self-hosted/local design. Agreed direction when this gets picked
      up: an OS-level scheduled task (Windows Task Scheduler) running
      `applysync sync` once a day, independent of whether the dashboard/API
      server is open.
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
