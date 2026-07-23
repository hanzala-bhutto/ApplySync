# ApplySync changelog

The full blow-by-blow history of every shipped milestone: what was built, the
bugs found along the way, and the reasoning behind each decision. This was
extracted from `CLAUDE.md` to keep that file a compact agent-context document;
the **durable rules** those milestones produced live in `CLAUDE.md`'s own
sections (Hard constraints, LangGraph pipeline nodes, LLM, Web search), not
here. Update those when a rule changes; append here when a milestone lands.

Ordered oldest-first within each phase.

## M1 - Gmail ingestion

### M1a: OAuth client, query builder, message parsing
Gmail OAuth client, query builder, message parsing (code done, tested).

### M1b: Manual extraction spike
Ran `backend/scripts/gmail_probe.py` against the real inbox. The initial design
(sender-domain allowlist: LinkedIn, Indeed, StepStone, jackandjill.ai) turned
out to be the wrong approach: LinkedIn Easy Apply and jackandjill.ai send no
confirmation emails at all, and direct/ATS confirmations (SmartRecruiters,
Personio, Ashby, join.com, Teamtailor, Rippling, Workday, onlyfy.jobs, direct
company domains) come from senders that can't be enumerated upfront. Redesigned
to search by `confirmation_keywords` (subject phrase) only, with no
sender-domain restriction; verified against the real inbox, 25/25 results were
genuine application confirmations with zero domain filtering. `sources.yaml`'s
`platforms`/`sender_domains` are now attribution-only. Also found and fixed two
real bugs: HTML-only emails (jackandjill.ai) extracted as empty bodies (no
text/plain part), and StepStone's Windows-1252 charset was being force-decoded
as UTF-8, mangling apostrophes/umlauts.

## M2 - LangGraph pipeline + SQLite persistence + idempotency

Built in three parts: M2a (SQLModel schema + repository), M2b (node factories,
unit tested with fake models), M2c (graph wiring + checkpointing). Verified
against the real inbox + real NVIDIA API (not just unit tests): correctly
classified a rejection whose subject said "thank you for your application"
(Nagarro), correctly attributed platforms via `sources.yaml` including a correct
fallback to "other" for a direct company domain (EGYM). Found and fixed one real
bug (job_title placeholder hallucination causing duplicate application rows) and
one real infra issue (free-tier rate limiting). Idempotency (double-run = 0 new
rows) and status-update-links-not-duplicates both verified by automated tests in
`tests/test_graph.py`.

## M3 - Dashboard

### M3a: FastAPI dashboard core
Status board, timeline, by-platform, reminders, server-rendered, verified
against real data.

### M3b: Interactive redesign
Tailwind CDN (chosen over Pico.css: a custom Kanban layout needs utility-class
control a classless framework doesn't give you; CDN build is a known
non-production tradeoff, fine for a personal local tool), SortableJS
drag-and-drop between status columns (`PATCH /applications/{id}/status`), HTMX
inline field editing (`PATCH /applications/{id}`), and a "reprocess from email"
action (`POST /applications/{id}/reprocess`, refetches the original Gmail
message by its stored id and re-runs extraction only, not the full graph). Found
and fixed a real schema-migration gap along the way: making
`StatusEvent.source_email_id` nullable doesn't apply to an already-existing
local `applysync.db` file (`create_all` only creates missing tables, never
alters existing ones) - there is no migration tooling yet, so a schema change
means deleting and recreating your local db for now.

### M3c: "Connect Gmail" button (web OAuth redirect flow)
So first-run and any future re-consent happen inside the dashboard, not the
terminal. `web/gmail_oauth.py`: `GET /api/gmail/status` (token
presence/validity check), `GET /api/gmail/connect` (builds a
`google_auth_oauthlib.flow.Flow` and redirects to Google's consent screen), `GET
/api/gmail/callback` (exchanges the code, writes `token.json`, redirects back to
wherever the user started from via a `return_to` param round-tripped through
OAuth `state`). Reused the existing Desktop-app `credentials.json` unchanged -
Google's loopback redirect rules (RFC 8252) accept any
`http://localhost`/`127.0.0.1` redirect URI for that client type, the same
mechanism `InstalledAppFlow.run_local_server(port=0)` already relied on, so no
separate "Web application" OAuth client was needed. Frontend:
`GmailConnectionBanner` in `Layout.tsx` shows a "Connect Gmail" banner (real
`<a>` navigation, not a fetch, since it has to walk through Google's own pages)
whenever disconnected, and handles the `?gmail=connected`/`?gmail=error`
redirect back with a toast + URL cleanup. `/gmail-setup` skill updated to
document both paths (CLI first-run still works for
`backend/scripts/gmail_probe.py`).

Three real bugs found testing the actual flow (not caught by the mocked backend
tests, since those never exercise a real token exchange or a real cross-origin
redirect):
1. `Settings.gmail_client_secrets_path`/`gmail_token_path`/`db_path` were
   relative paths resolved against whatever directory the process happened to be
   started from, not the repo root - harmless for the CLI (always run from repo
   root by convention) but `/api/gmail/connect` returned 500 "No Gmail client
   secrets file found" because the running server's cwd wasn't the repo root
   even though the file existed there. Fixed with a `field_validator` in
   `config.py` that resolves relative paths against `PROJECT_ROOT`.
2. `invalid_grant: Missing code verifier` from Google on the callback -
   `google-auth-oauthlib` defaults `autogenerate_code_verifier=True`, so the
   `/connect` route's `Flow` instance generates a PKCE `code_verifier` and sends
   its `code_challenge` to Google, but `/callback` built a *separate* `Flow`
   instance (different HTTP request, no shared state) with no verifier, so
   `fetch_token()` sent none. Fixed by storing `flow.code_verifier` alongside
   `return_to` in `_pending_states` (keyed by OAuth `state`) and passing it into
   the callback's `Flow.from_client_secrets_file(..., code_verifier=...)`.
3. `GmailConnectionBanner` built `return_to` as just `window.location.pathname +
   search` (e.g. `/`), a relative path - when the backend's callback issued
   `RedirectResponse(return_to + "?gmail=connected")`, the browser resolved that
   relative URL against the BACKEND's own origin (the origin the redirect
   response came from), not the frontend's, landing on
   `http://127.0.0.1:8001/?gmail=connected` (404, no such backend route) instead
   of back on the Vite dev server. Fixed by including `window.location.origin` in
   `return_to`, so it's always an absolute URL; the Playwright test for the
   banner now asserts `return_to` starts with `http(s)://` to catch this class
   of regression.

## Perf + accuracy pass (post-M3)

Triggered by the user's real 238-application inbox only showing ~7-8
applications: fixed Gmail pagination (was silently capped at 50 emails ever),
merged classify+extract into one LLM call, switched to nemotron-3-nano-30b-a3b
with reasoning off (~9x faster), then found and fixed a real accuracy regression
from that speed change (see the LLM section in `CLAUDE.md`) before it could
corrupt real data further. Bulk-reprocessed all 237 real applications with the
corrected pipeline: 174 corrected, 13 deleted as false positives.

## React migration (Jinja2 -> React SPA)

### 1/4: JSON API
`web/api.py` adds `/api/dashboard`, `/api/applications/{id}` (GET/PATCH),
`/api/applications/{id}/status` (PATCH), `/api/applications/{id}/reprocess`
(POST), reusing repository.py/pipeline logic unchanged. CORS matched by regex
(`http://(localhost|127.0.0.1):\d+`), not a fixed port - Vite falls back to the
next free port whenever another project's dev server already holds 5173, which
happened for real during this build. Existing Jinja2 dashboard still works
unchanged, both run side by side during migration.

### 2/4: scaffold + read-only dashboard parity
`frontend/`: Vite (pinned to v6 - the new default "rolldown-vite" v8 release has
a broken native binding on this machine, a real, reproducible build failure, not
a hypothetical) + React + TypeScript + Tailwind v4 + TanStack Query +
react-router. Dashboard/detail pages ported from the Jinja2 templates (avatar
colors, status styling, filters via URL search params). TypeScript compiles
clean, production build succeeds, backend verified against the real
230+ application dataset via curl.

### 3/4 (planned scope, folded into 4/4a)
Interactivity requirements the user surfaced from NNGroup's 10 heuristics + a
2026 UX principles piece: undo affordance after a drag-and-drop status change
(not just optimistic-update-and-hope), confirmation before destructive actions
(reprocess overwriting fields), visible loading/status feedback, plain-language
error messages (not raw fetch/HTTP errors), keyboard-operable drag-and-drop.

### Jinja2 dashboard removed (ahead of full React parity)
The user explicitly chose to cut over once the read-only React dashboard worked,
rather than wait for drag-and-drop/edit/reprocess to be ported first.
`web/app.py` is now just CORS + API registration; all response shapes get
explicit Pydantic response models (`web/api.py`: `DashboardResponse`,
`ApplicationDetailResponse`, `PlatformBreakdownRow`, `FilterOptionsResponse`) so
FastAPI's auto-generated Swagger UI (`/docs`) and OpenAPI schema
(`/openapi.json`) are actually useful, not just raw untyped dicts.
`python-multipart`/`jinja2` dropped from dependencies.

### 4/4a: interactivity + animation
- Drag-and-drop status correction: `@dnd-kit/core` (not `@dnd-kit/sortable` - we
  need cross-column moves, not in-column reordering). `PointerSensor` with
  `activationConstraint: {distance: 8}` so a card is still a normal clickable
  `<button>` (small movements are clicks, not drags). Optimistic update via
  TanStack Query's `onMutate`/`onError` (instant board update, rollback on
  failure) plus a success toast with an **Undo** action (re-mutates back to the
  old status) - drag-and-drop must never be a one-way door, per the NNGroup "user
  control and freedom" heuristic.
- **Keyboard drag conflict, found and fixed**: dnd-kit's default `KeyboardCodes`
  bind *both* Space and Enter to start/end a drag. Since cards are real
  `<button>`s (Enter = native click = open detail page), leaving Enter bound to
  drag-start would fight the button's own keyboard behavior. Fixed by passing
  `keyboardCodes: { start: ['Space'], cancel: ['Escape'], end: ['Space'] }` to
  `useSensor(KeyboardSensor, ...)` - Enter opens, Space grabs/drops.
- Base `@dnd-kit/core`'s keyboard coordinate getter moves the dragged item by
  fixed pixel deltas per arrow press, not "snap to nearest column" - reaching a
  distant column by keyboard may take several presses. Accepted as a known
  limitation rather than writing a custom coordinate getter; the `<select>` on
  the detail page is the fully robust keyboard/screen-reader path for the same
  action, per the accessibility principle that drag-and-drop should never be the
  *only* way to do something.
- Detail page: inline edit form (toggle, not always visible), a plain `<select>`
  for status (the robust non-drag path), and a reprocess button gated behind
  `ConfirmDialog` (native `<dialog>` element - free focus
  trapping/ESC-to-close/correct screen-reader dialog semantics, not hand-rolled
  ARIA).
- `lib/toast.tsx`: a small `aria-live="polite"` toast system (own code, not a
  dependency) supporting an optional action button, used for every mutation's
  success/error feedback.
- Framer Motion's `layout` prop on each card animates the position shift when a
  card moves between columns.

### 4/4b: Playwright E2E tests + accessibility audit
`frontend/e2e/` (`@playwright/test` + `@axe-core/playwright`, config at
`frontend/playwright.config.ts`): every test mocks `/api/*` via `page.route`
(`e2e/fixtures.ts`) instead of hitting the real FastAPI backend or a real
Gmail-derived database, so the suite is self-contained and reproducible.
Playwright's `webServer` builds and runs `vite preview` for the test run only
(`npm run test:e2e`), then tears it down automatically. 12 tests across three
files: dashboard rendering/filtering (including a direct regression test for the
keepPreviousData fix, delaying the mocked response to assert the old board stays
visible), application detail (status badge/select decoupling, edit form,
reprocess confirm-dialog centering and cancel path), and keyboard operability
(Space drags, Enter opens). `webServer.url` and `baseURL` had to use
`http://localhost:4173` rather than `127.0.0.1:4173` - the latter didn't resolve
fast enough on this machine and Playwright's server-ready check timed out. Axe
found real WCAG 2 AA color-contrast failures (not false positives): several
`text-slate-400`-on-light-background instances fell below the required 4.5:1
ratio - all had the class order backwards (`text-slate-400 dark:text-slate-500`,
meaning dark mode got the *lighter* shade and light mode got the one that needed
to be darker). Fixed by swapping to `text-slate-500 dark:text-slate-400` (and
`text-slate-300 dark:text-slate-600` -> `text-slate-500 dark:text-slate-500` for
the empty-column placeholder). Known remaining gap: axe-core catches
contrast/ARIA/semantic issues but is not a substitute for an actual screen-reader
pass (NVDA/VoiceOver) on the live app.

### Frontend polish pass (post-4/4b)
Fixed the leftover Vite scaffold favicon/title (`frontend/index.html` still said
"frontend", `public/favicon.svg` was still the default Vite logo - replaced with
an ApplySync-branded icon and title). Lightened dark mode: the whole
neutral/status color scale was one step darker than needed, shifted every
dark-mode shade up one step across `Layout.tsx`, `Dashboard.tsx`,
`ApplicationDetail.tsx`, `ConfirmDialog.tsx`, `status.ts`, `toast.tsx` - light
mode untouched. Fixed follow-up reminders not scaling:
`repository.stale_applications` had no limit or ordering and the dashboard
rendered every stale application in one unbounded grid. Dashboard now shows a
bounded preview (`REMINDERS_PREVIEW_SIZE = 6`, oldest-first) with a "View all N"
link; a new dedicated `/reminders` page + `GET /api/reminders` endpoint does real
DB-level pagination (`repository.stale_applications_page`/
`stale_applications_count`, `offset`/`limit` in SQL, not in-memory slicing).
Split the platform response-rate breakdown into its own `/analytics` page, added
a nav bar to `Layout.tsx` (Dashboard / Follow-Up / Analytics). Renamed vague
headings: "Pipeline" -> "Application Pipeline", "Follow-up reminders" -> "Needs
Follow-Up", "By platform" -> "Response Rate by Platform".

## Feature additions

### Source email verification
The application detail page had no way to see the email an extraction actually
came from, only the LLM's output. Added `GmailClient.get_message(message_id)`
(refactored out of `reprocess_application`'s inline fetch, now shared) and `GET
/api/status-events/{event_id}/email` (looks up the event's `source_email_id`,
live-fetches from Gmail, returns subject/sender/date/body - nothing new stored in
the DB, a read-through, same pattern as reprocess). Each Timeline row got a "View
email" toggle (only shown when the event has a `source_email_id`; manual
corrections show "manual" instead) that expands an inline panel: truncated to 500
chars by default with a "Show full email" toggle.

### `declined` status
User-reported (e.g. Tekscend Photomask Germany GmbH - an offer/interview the user
turned down themselves, distinct from `rejected` which means the company said
no). Manual-only by design: added to `repo.STATUS_ORDER` and `STATUS_STYLES`
(frontend, orange to stay distinct from `rejected`'s rose and `other`'s amber),
but deliberately NOT added to the LLM's status `Literal` in `pipeline/state.py` -
declining is the user's own action, never something stated in an inbound email,
so the classifier should never be able to produce it. Set via the status dropdown
or drag-and-drop like any other manual correction (`set_manual_status` already
accepted arbitrary strings).

### Manual "Sync Now" button (M4 precursor)
The user chose this over automatic scheduling for now. `web/sync.py`: `POST
/api/sync` starts `run_sync` in a background `threading.Thread` (not
`BackgroundTasks` - a full sync can take minutes at the 40 RPM rate-limit floor,
and this needs to survive being kicked off from a request that returns
immediately) and returns 202; a module-level lock + dict (`_state`) rejects a
second concurrent sync with 409 rather than queuing or double-running one. `GET
/api/sync/status` reports `in_progress`/`last_error`/`latest_run` (via new
`repo.get_latest_pipeline_run`). `run_sync` is dependency-injected via
`get_run_sync` so tests can swap in a fake. Frontend: `SyncButton` in
`Layout.tsx` shows last-synced time, polls `/api/sync/status` every 1.5s only
while `in_progress`, and toasts the outcome.

Also fixed a UX/accessibility inconsistency: the sync failure toast showed the
raw backend exception text instead of a plain-language message (now generic,
matching every other mutation's error toast), and every real `<button>` app-wide
was missing `cursor-pointer` (Tailwind v4's preflight doesn't add it) - fixed
everywhere.

Two more real bugs found by actually clicking "Sync Now" against the real inbox:
(1) a real rejection email ("Your application at dexter health") wasn't fetched
at all - the Gmail search is subject-only, and its subject didn't match any
`confirmation_keywords` phrase (closest was `"your application for"`, but this
one said "at"). Added `"your application at"` to `backend/config/sources.yaml`.
(2) Fixing (1) alone wasn't enough: the two zero-result manual syncs run while
testing still completed "successfully" (0 emails found, but `finished_at` got
set), which advanced `last_successful_run_started_at` to that day. Since Gmail's
`after:` filter is date-only, the next sync would have used `after:` today's
date, permanently excluding the July 5 email even after the keyword fix. Fixed by
adding `SYNC_LOOKBACK_BUFFER_DAYS = 3` in `graph.py`, subtracted from the last
run's date on top of the existing same-day overlap - cheap (the
`processed_emails` idempotency table already dedupes anything re-fetched in the
wider window), and closes this whole class of edge case.

### Pipeline redesign (GitHub issues #17-21)
First time this project used pushed branches + `gh pr merge` end to end instead
of local-only merges. Broadened Gmail keyword filter, concurrent Gmail fetch, a
new `scrutinize_relevance` LangGraph node, `PipelineRun` incremental progress
fields, and a dedicated `/sync` staged-progress page.
- **#17**: `sources.yaml`'s `confirmation_keywords` gained single-word matches
  (`applied`, `interview`, `rejected`, `offer`, etc.) alongside the existing
  exact phrases, so emails whose subject doesn't match a known phrase (a real
  rejection from "dexter health" was missed entirely before this) still get
  fetched.
- **#18**: `GmailClient.fetch_messages`'s per-message body fetch is now a
  10-worker `ThreadPoolExecutor` instead of sequential. Each worker thread builds
  its own Gmail API service instance rather than sharing one, since
  googleapiclient's httplib2 transport isn't documented as thread-safe.
- **#19**: broadening the keyword filter alone would let more job-alert digests
  reach the LLM-rate-limited `classify_and_extract` call, so a new
  `scrutinize_relevance` node is now the graph's entry point - a hybrid heuristic
  (instant reject on digest markers, instant pass on the original narrow phrases)
  + one cheap `RelevanceOnlyResult` LLM call only for genuinely ambiguous
  subjects. Fails open (pass) on an LLM error. Rejected emails are marked
  processed with `classification="scrutiny_rejected"`.
- **#20**: `PipelineRun` gained
  `emails_total`/`emails_scrutinized`/`emails_extracted`/`emails_written`/
  `updated_at`, added to an existing `applysync.db` via a new additive `ALTER
  TABLE` migration pass in `init_db`. `process_emails` switched from
  `compiled.invoke` to `compiled.stream(stream_mode="updates")` so progress is
  observable node-by-node.
- **#21**: new `/sync` page (`frontend/src/pages/Sync.tsx`) shows a 4-stage
  progress view plus a recent-run history table, reusing `SyncButton`. `GET
  /api/sync/status` gained an optional `history` field.

### Full Audit (shipped 2026-07-06 as `full_scan`, renamed 2026-07-19)
See `docs/feasibility/full-audit-rename.md`. Re-runs today's pipeline against
every email ever seen, not just new ones - lets a prompt/model change be
validated against the real historical inbox instead of only new mail.
`pipeline/full_audit.py`: `full_audit()` refetches every id in
`processed_emails` via `GmailClient.fetch_messages_by_id`, and
`process_full_audit()` reuses only the two side-effect-free node factories,
`scrutinize_relevance` and `classify_and_extract` (called directly as plain
functions, not through the compiled graph) - it never touches
`match_existing_application`, `disambiguate_match`, or `upsert_db`. This is
deliberate: those three auto-decide and auto-write, and running the full
write-capable graph over the *entire* historical inbox in one pass would let a
rare LLM false-positive silently corrupt real data at scale. Instead, every
disagreement between the fresh re-extraction and what's stored
(`_application_differs`, comparing against the specific status event the email
originally created, not `application.current_status`) becomes a
`ReviewSuggestion` row a human approves or rejects on the `/review` page
(`web/review.py`), never applied automatically.
`has_pending_suggestion_for_message` guards against re-flagging the same email
across repeated/crashed runs. Triggered from the `/sync` page's "Run Full Audit"
button (`POST /api/sync/full-audit`), gated behind the same in-progress lock as a
normal sync. Two real bugs fixed after running against the real inbox: a crash on
manually-`declined` applications (that status is excluded from the LLM-output
schema on purpose, but the diff snapshot was built through that same schema,
tripping a validation error), and a false-positive flood (528 suggestions from
~460 real emails) from comparing against `application.current_status` instead of
the specific email's own original status event, which false-flagged every older
email on any application with more than one status transition.

## Web research

### Foundation: self-hosted SearXNG
`backend/applysync/search/client.py`. Live-verified against a real query.

### Company research card (first web-research feature)
`POST /api/applications/{id}/research` -> `backend/applysync/research/company.py`
searches SearXNG for the company and synthesizes a grounded profile
(summary/industry/size/HQ/website/recent_news) plus the source URLs it was
grounded in; cached in the new `CompanyProfile` table keyed by company name
(shared across applications), `refresh=true` forces a re-fetch. Frontend: a
visually-distinct, clearly-web-labeled card on the detail page
(`ResearchCard`/`ResearchResult` in `ApplicationDetail.tsx`). **Key finding,
verified against the real model, not mocks**: this model (`nemotron-3-nano`)
returns an all-empty object from `with_structured_output` (tool-calling) once the
schema has any list field, and is unreliable for optional-heavy schemas generally
- the same model produces a complete, correct profile via `PydanticOutputParser`
over its plain-text output. So `CompanyProfileResult` is flat scalars only
(`recent_news` is text, not a list) and research uses the parser, not
`with_structured_output`. Do not "simplify" it back without re-checking against
the real model. Data-integrity rule enforced: web-sourced profile lives in its
own table and its own response model/card, never merged into `Application`.

### Entity/duplicate resolution (first genuinely agentic feature; issue #48)
Conditional branch off `match_existing_application` in `pipeline/graph.py`:
`make_match_node` emits `candidate_ids` (same company+platform, any title, via
`repo.find_candidate_applications`) when the exact-title match misses but
candidates exist - the documented missing-title-vs-different-title gap
(Nagarro/EGYM). A new conditional edge routes that ambiguous case to
`disambiguate_match`; clear new/update cases go straight to `upsert_db`. The
agent (`backend/applysync/research/disambiguate.py`) is a **hand-rolled LLM tool
loop**, not `create_react_agent`: it binds tools (`get_status_history`,
`read_source_email` which fetch+diffs a candidate's source email vs. the new one
via `GmailClient.get_message`, `web_entity_check` over SearXNG) and loops until
the model calls a terminal `submit_verdict` tool (bounded by `MAX_AGENT_TURNS=8`
for the 40 RPM cap - raised from 6 after a real full-history resync showed ~25%
of ambiguous cases hitting the old limit and failing open, largely from the model
repeating `web_entity_check` instead of using the cheaper
`get_status_history`/`read_source_email` first). The verdict maps onto the
existing `MatchDecision` (`same_application`->update_existing,
`different_application`->new_application, `duplicate`->duplicate_skip) and its
rationale is stored on the resulting status event's `notes`. **Key model finding:
this model's native tool-calling IS reliable for scalar-arg tools** - the
documented brittleness is list-field-specific to `with_structured_output`. Fails
**open** to a new application on any agent/search/LLM error (recoverable, unlike a
wrong merge). `gmail_client`/`search_client` are threaded into `build_graph` as
optional deps; without them the ambiguous case falls open instead of routing to
the agent. No new DB columns; `processed_emails` idempotency guard intact. Tested
in `tests/test_disambiguate.py`.

**LLM-as-judge accuracy audit + date-arithmetic fix (2026-07-20,
`docs/feasibility/disambiguation-date-arithmetic.md`)**: the M5 LLM-judge
evaluators were run for the first time in anger against a real full-history sync
session (500 emails, `backend/scripts/run_llm_judge_backfill.py` - written
because the self-hosted Langfuse build's Traces-table "Actions -> Evaluate"
backfill button, which the hosted docs describe, isn't present in this version;
the script gets the same result via the public API directly). Results: relevance
99.5% (568/571), extraction 96.4% (511/530), disambiguation 79.5% (97/122) - the
low disambiguation number turned out to be mostly a **measurement bug, not an
agent bug**: the `disambiguate_match` observation's own input/output doesn't
include what `get_status_history`/`read_source_email` actually returned (sibling
trace observations), so the judge was scoring "hallucination" on reasoning it had
no way to verify. Feeding the judge the real tool-call evidence raised the honest
baseline to 91.0% (111/122). Of the 11 truly-flagged cases, 10 shared one root
cause: the agent doing mental arithmetic on two raw RFC-2822 date strings and
getting the chronological order backwards. Fixed by computing the exact day delta
in Python (`_relative_to_new_email`) and annotating both tools' returned dates
inline (e.g. "5 days AFTER the new email"), rather than asking the model to do the
subtraction. Verified live with `backend/scripts/replay_disambiguation.py`
against all 10 flagged cases: 5 flipped to the judge-endorsed correct verdict, 3
kept their verdict but replaced fabricated reasoning with real computed evidence,
2 remain open on a separate root cause (which-of-several-candidates ambiguity, not
date math). Extraction's 19 flagged cases also surfaced two new not-yet-fixed
patterns: Indeed confirmation emails wrongly returning `missing_required_fields`
(6 cases) and the "similar jobs" recommendation-section guardrail not holding for
Stepstone's specific template (4 cases) - pulled into `eval/samples/gold.json` as
unverified samples via `pull_flagged_traces.py`.

### Fuzzy/alias company matching
Closes the exact-string blind spot in
`repo.find_matching_application`/`find_candidate_applications` that let real typo
dupes ("EGYM" vs "EGYG") and word-add dupes ("Galvany" vs "Galvany Energy") slip
through as separate rows. `repository.py`: `_company_names_match(a, b)` is
`fuzz.ratio(a, b) >= 75` (new `rapidfuzz` dependency; the typo path - chosen
because a 1-char edit on a short name like "egym"/"egyg" only scores ~75 on any
string metric, a higher bar would miss the real case) **OR** a strict
token-subset check (`_is_company_token_subset`, the word-add path: every word of
the shorter normalized name must appear in the longer one). Title must still
match exactly regardless of company score, so two different roles at the same
company still stay distinct. `find_exact_company_applications` (renamed from the
old `find_candidate_applications`) stays EXACT-company-only and backs
`find_matching_application`, so a fuzzy-only company hit - even with an exact
title match - can never auto-resolve to `update_existing`; it always falls
through to `find_candidate_applications` (now fuzzy) and routes to the
disambiguation agent. **Real false positive found by running the extended cleanup
script against the live database**: an earlier version scored company similarity
with `max(fuzz.ratio, fuzz.token_set_ratio)`, and `token_set_ratio` alone matched
"Cloud&Heat Technologies GmbH" and "Nash Technologies" at 82.8 purely because
they share the generic word "technologies". Fixed by replacing the
token_set_ratio path with the stricter subset check.
`backend/scripts/merge_duplicate_applications.py` extended with a second pass
(`find_fuzzy_duplicate_groups`, exact-title bucket + union-find over
fuzzy-matching companies within it). Feasibility:
`docs/feasibility/fuzzy-company-matching.md`.

### Cross-provider disambiguation agent + requisition-ID short-circuit
`docs/feasibility/cross-provider-disambiguation-agent.md`, PR #103, merged
2026-07-23. The req-ID short-circuit is unit-tested in `tests/test_disambiguate.py`
(extraction bounds, single-match short-circuit-without-model, multi-match
defer-to-agent); the Groq provider wiring itself has no dedicated test, since it's
a config-gated swap of the model object with a `.with_fallbacks()` composition.
Two changes, both scoped to `disambiguate_match` only (extraction/scrutiny
untouched):
1. **Optional Groq hybrid**: `llm.get_agent_model` returns a `ChatGroq` when
   `groq_api_key`+`groq_agent_model` are both set (else `None`), and
   `make_disambiguate_node` prefers it (`llm = agent_model or escalation_model or
   model`) with the NVIDIA escalation model composed as a runtime
   `.with_fallbacks()` fallback in `run_disambiguation` - Groq is fast (~1-3s vs
   15-30s under NVIDIA rate contention) and has its own 30 RPM account budget, so
   the agent stops competing with bulk extraction for NVIDIA's 40 RPM; a Groq
   429/outage transparently falls back to NVIDIA. The shared limiter is now
   per-RPM (`_limiter(rpm)`), one instance each for NVIDIA-40 and Groq-30.
2. **Requisition-ID short-circuit** (`_extract_req_ids`, `_REQ_ID_RE` = 5-8 digit
   numbers): when the new email and exactly one candidate share an ATS req ID,
   it's decided as `same_application` (high confidence) in Python before the model
   runs - an A/B on real data surfaced a wrong verdict on a clear same-application
   pair (shared SAP req ID) that no prompt wording fixed. Provider-agnostic;
   several matches still fall through to the agent.

`agent_model` is threaded as an optional dep through
`build_graph`/`compile_graph`/`process_emails`/`run_sync` (defaults `None`).

## M5 - Reliability push

The user explicitly chose reliability over new features after a full-history
resync surfaced the status-ordering bug, the process-step-title extraction bug,
and a real disambiguation wrong-merge. Agreed order, each its own feasibility
report + issue + PR:

### 1. Eval harness + gold dataset (issue #68)
`backend/applysync/evaluation/scoring.py` (pure metric logic),
`backend/scripts/build_eval_dataset.py` (150 samples from the real inbox, labels
pre-filled from the pipeline's own stored output, human-verified before they
count), `eval/run_eval.py` (replays verified samples through the real scrutiny +
classify_and_extract nodes; per-stage metrics: scrutiny false-reject rate with a
zero budget, relevance accuracy, per-field extraction accuracy using matching's
own normalization; `--strict` for use as a pre-merge gate). Samples hold real
email bodies, so `eval/samples/*.jsonl` is gitignored. The recorded baseline only
exists after the user's label-verification pass over gold.jsonl. Matching/dedupe
(merge precision/recall) eval is a documented follow-up - it needs seeded DB state
per sample.

### 2. Langfuse observability, self-hosted (issue/PR #77)
NOT LangSmith: hosted SaaS would ship email bodies off-machine. `langfuse/`
(docker-compose.yml: the official v3 stack - Postgres, ClickHouse, Redis, MinIO);
`backend/applysync/observability.py` (`get_langfuse_handler`, DI-style, returns
`None` and disables tracing whenever `LANGFUSE_PUBLIC_KEY`/`SECRET_KEY` are unset
or init fails - tracing is diagnostic, never load-bearing). Threaded into
`run_sync`/`process_emails`'s stream config (LangChain propagates callbacks to
nested `.invoke()` calls automatically, so one handler traces every node plus the
disambiguation agent's whole tool loop) and explicitly into `research_company`.
Each email's trace is tagged with the sync's `run_id` as a Langfuse session.
`backend/scripts/pull_flagged_traces.py` closes the loop from "noticed this is
wrong in the Langfuse UI" to the eval harness tracking it: score a trace false on
a Boolean score config and this script pulls it into `eval/samples/gold.json` as
an unverified sample. PR #83 upgraded the original single `correct` score to three
per-stage ones
(`relevance_correct`/`extraction_correct`/`disambiguation_correct`) plus
LLM-as-judge evaluators (one per stage, fully MIT since Langfuse v3.65+) that can
auto-populate those scores - configured in Langfuse's own UI (Settings/Evaluators,
not files; judge prompts kept as a version-controlled reference copy in
`docs/feasibility/langfuse-judge-prompts.md`) for **manual/backfill triggering
only, not live** - a live evaluator would call the judge model from the Langfuse
worker container, uncoordinated with the pipeline's own shared rate limiter,
risking budget contention during a real sync.

Two real bugs found bringing the stack up: a local Postgres install already held
port 5432 (remapped to 15432, host-only); omitting `CLICKHOUSE_CLUSTER_ENABLED=false`
defaults the image to cluster mode, which runs migrations `ON CLUSTER default` and
fails with no Zookeeper configured. A third, found later when ClickHouse's
`latest` tag drifted mid-project (issue/PR #81): ClickHouse 25.x+ changed its
default query analyzer, breaking Langfuse's own `scores.all` query (500 on the
Scores page) - fixed by pinning the image and mounting a `users.d` config forcing
the legacy analyzer, which required `CLICKHOUSE_SKIP_USER_SETUP=1` (the image's
entrypoint otherwise regenerates that mounted path from env vars on every restart,
silently overwriting the committed config with a plaintext-password version) and
re-declaring the `clickhouse` user with its password sourced via
`from_env="CLICKHOUSE_PASSWORD"`. **Real gotcha discovered operating the stack**:
since `users.d` is bind-mounted from the git working directory, checking out a
branch that lacks these files deletes them live out from under the running
container, breaking ClickHouse auth immediately with no restart needed to trigger
it - resolved by getting the fix merged promptly, not by changing the mount
strategy.

### 3. Confidence-routed merges
The disambiguation agent's `submit_verdict` now carries a `confidence` enum
(high/medium/low, an enum not a float since this model does unreliable numeric
reasoning). A `same_application`/`duplicate` verdict below
`settings.disambiguation_min_auto_merge_confidence` (default "medium", so only
"low" routes) is no longer applied silently: the email is written as a NEW
application (recoverable, the same fail-open direction the agent's error path
takes) and a `merge_into` `ReviewSuggestion` is queued for a human. Approve
collapses the new row into the candidate via a new
`repo.merge_applications(source_ids, target_id)` (extracted from the
duplicate-cleanup script's `merge_group` so there's one merge implementation,
delete-sources-before-updating-target to avoid the UNIQUE-tuple collision); reject
and reject-all stay pure no-ops. `ReviewSuggestion` gained a `confidence` column
(additive migration generalized in `init_db`). The `/review` card shows a
confidence badge + a merge explanation. Feasibility:
`docs/feasibility/confidence-routed-merges.md`.

### 4. Tiered models (issues/PRs #72/#75, #74)
`settings.llm_escalation_model` (the larger, slower model this project ran before
switching to nano for speed) now serves three narrow, mechanical escalation paths
rather than a model swap everywhere - the fast nano model still handles the vast
majority of calls. (a) `scrutinize_relevance`'s rare ambiguous-case call goes
straight to the escalation model. (b) `classify_and_extract` gets one
escalation-model retry when the fast call fails outright or returns relevant with
no usable company name. (c) `make_disambiguate_node` always prefers the escalation
model when configured (unconditional, not failure-gated - this node is already
low-volume, ~50 calls per full sync vs 500 for extraction, and a wrong merge
verdict is unrecoverable the way a redundant new row is not). Verified against the
eval baseline: scrutiny false-rejects 1.9% -> 0.0%, classification accuracy 79.8%
-> 98.2%, status 92.9% -> 95.3%. The 98.2% number also caught and fixed a severe
pre-existing bug (see relevance-classification-accuracy below) - measuring against
a real baseline is what finally surfaced it.

### 5. Shared NVIDIA rate limiter (issue/PR #79)
Found via real Langfuse traces - escalation-model calls occasionally took 15-30s+
instead of the usual 1-4s, the signature of `with_retry`'s backoff kicking in
after a 503. `get_chat_model` built a fresh `InMemoryRateLimiter` on every call,
so the fast and escalation models each got their own independent 40 RPM budget,
but NVIDIA's cap is per-account, not per-model - the combined real request rate
could exceed 40 RPM even though each limiter thought it was compliant. Fixed with
one process-wide `lru_cache`'d limiter shared by every `ChatNVIDIA` instance.
Directly validates the Langfuse investment: this class of latency bug was
invisible before per-call tracing existed.

### 6. LLMOps automation (`docs/feasibility/llmops-pipeline.md`)
The reliability pieces (eval harness, Langfuse, judge backfill, flagged-trace
pull) existed but ran only when a human remembered to. This wires them into an
enforced, **two-plane** setup, because the gold dataset is PII (gitignored) and
the eval hits the live rate-limited model, so the eval CANNOT run in cloud CI:

| Check | Plane | PII/live model? |
| --- | --- | --- |
| pytest + lint + frontend build/E2E | GitHub Actions (`.github/workflows/ci.yml`) | no (LLM + `/api/*` mocked) |
| prompt/schema-drift guard (`tests/test_schema_drift.py`, locks the classifier status `Literal`) | GitHub Actions | no |
| eval gate (`eval/run_eval.py --strict`) | local | yes |
| pre-push enforcement (`scripts/hooks/pre-push`, `scripts/install-hooks.sh` sets `core.hooksPath`) | local git hook | yes |
| quality-over-time ledger (`--ledger` -> `eval/baseline.json`) | local, committed | no (aggregate only) |

The pre-push hook diffs the pushed range for prompt/model-affecting paths
(`pipeline/nodes.py`, `pipeline/state.py`, `sources.yaml`, `config.py`,
`research/`) and only then runs `--strict`; bypass with `git push --no-verify`.
`eval/baseline.json` is the ONE eval artifact allowed on the public repo and is
**aggregate-only** by construction (`report_to_ledger` emits numbers/date/sha/
model, never a message ID, email body, or company name - a leak check guards
this). CI lint is scoped to `backend/applysync eval tests` (the ad-hoc
`backend/scripts/` carry intentional `sys.path`-setup E402s). The AI-engineering
competency scorecard lives in `docs/llmops-scorecard.md`, linked from the README.

## Accuracy / correctness fixes

### Status-ordering and job-title extraction fixes (issue/PR #67)
Found by this project's first full historical resync - hundreds of emails
processed in one batch surfaced two bugs incremental syncs had masked. (1)
`add_status_event` unconditionally overwrote `current_status` with whichever
event was *processed* last, but Gmail's search API returns results newest-first,
not chronologically - a batch sync could let a chronologically older email win
over already-recorded later ones (confirmed: an application stuck on `applied`
despite `rejected`/`interview` events already on record). Now only advances
`current_status` when the new event's `event_date` is the latest on record;
manual corrections still always win via `event_date=now()`. (2) The model
sometimes extracted the *type* of interview/process step ("Technical Interview",
"AI-powered video interview") as `job_title` instead of the real role - a
regex-based net (`_PROCESS_STEP_JOB_TITLE_RE`) normalizes these to the unspecified
sentinel, alongside sharpened extraction-prompt guidance.

### Relevance-classification accuracy fixes (issue/PR #72/#75)
Found by the eval harness's first real baseline: 150 verified real emails scored
only 79.8% classification accuracy, every single false negative a rejection email
wrongly marked irrelevant. `classify_and_extract` systematically marked rejection
emails (English and German) as `is_relevant: False` - silently dropping roughly a
quarter of real application outcomes every sync, worse than a duplicate row since
the application looks correctly "applied" forever. Fixed by rewriting STEP 1 of
the extraction prompt to explicitly separate "was an application submitted"
(`is_relevant`) from "is the news good or bad" (`status`) - a rejection is
exactly as relevant as an acceptance. Also fixed: a scrutiny-heuristic ordering
bug (an incidental "job alert" substring in unrelated footer text rejected two
real Wolters Kluwer confirmations before the correct confirmation-phrase check
ran), added `_normalize_company_name` placeholder defense, and gender/diversity-
qualifier stripping ("(m/f/d)") in title matching. One prompt change was tried and
reverted after measurement: excluding trailing ATS reference numbers from
`job_title` caused the model to over-truncate real title content after any
dash/qualifier - a regression only the eval harness caught. Verified: scrutiny
false-rejects 1.9% -> 0.0%, classification accuracy 79.8% -> 98.2%, status 92.9%
-> 95.3%, company steady at 95.3%.

### Real-time pipeline flow visualization and sync controls (PR #89, closes #86/#87/#88)
Three pieces: (1) renamed `full_scan` to `full_audit` throughout (module,
functions, API endpoint, DB `run_type` value, frontend labels, tests, with
backward-compat display handling for older stored rows) - "full-scan" read as a
bigger version of "sync," when it actually never writes to
`Application`/`StatusEvent` directly. (2) A live React Flow graph on the `/sync`
page (two tabs, Sync/Full Audit) mirroring the real LangGraph structure
node-for-node, color-coded by component type, with real-time node/edge highlight
animation driven by a new SSE stream and a diagnostic-only backend pub/sub (never
load-bearing - a disconnected client doesn't affect the run). Found and fixed
several real bugs verifying this live: a Chromium dynamic-SMIL-animation quirk,
React batching silently dropping a node's highlight, multiple edges sharing a
target node animating together regardless of which branch an email took, a WCAG
contrast regression, and a layout bug hiding the last node below the fold. (3) A
**Stop button** for an in-progress sync - cooperative cancellation checked between
emails, `POST /api/sync/stop`, a "Stopped" vs "Failed" display distinction. Found
and fixed one real bug live: cancelling a run partway through a large batch
advanced the sync bookmark to the cancelled run's own start time, permanently
hiding every not-yet-processed email from future syncs -
`last_successful_run_started_at` now excludes any run with `errors` set, not just
unfinished ones.
