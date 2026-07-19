# Real-time pipeline flow visualization on the Sync page

## Motivation
The `/sync` page currently shows four coarse aggregate counters (Ingestion/Scrutiny/Extraction/Classification-DB-Write). That answers "how far along is this run" but not "what is actually happening to this email right now" - which node is running, which model tier it used, which branch it took. The pipeline itself is already legible in code (`pipeline/graph.py`'s explicit conditional edges, small single-responsibility nodes per the project's design principle) - the sync page doesn't yet reflect that legibility back to the user.

## Problem
Debugging a wrong classification or a bad disambiguation verdict today means going to Langfuse and reading a trace after the fact. There's no live signal, during a sync, of which emails are hitting the escalation model, which are getting rejected at the cheap heuristic stage vs. the expensive LLM stage, or which are landing in the agent's ambiguous-match branch. A visual, real-time view of emails moving through the actual graph topology (not a simplified happy path) would make pipeline behavior observable as it happens, not just after the fact in Langfuse.

## Solution
Two pieces:

1. **Static graph** - a React Flow (`@xyflow/react`) diagram whose nodes/edges are generated directly from `pipeline/graph.py`'s structure: `scrutinize_relevance -> classify_and_extract -> match_existing_application -> upsert_db`, plus the branch edges to `mark_scrutiny_rejected`, `mark_irrelevant`, `mark_extraction_failed`, and the conditional `disambiguate_match` branch (present only when `agent_available`). Nodes are color-coded by component type (plain function / fast-model LLM call / escalation-model LLM call / agent tool-loop), verified against `nodes.py`, not guessed.
2. **Live event stream** - `process_emails` already runs `compiled.stream(stream_mode="updates")` per email (`graph.py:275`), yielding exactly `{node_name: output}` as each node fires. Forward each yield, tagged with the email's `message_id`, through a new `GET /api/sync/stream` SSE endpoint. SSE, not WebSocket, because this is one-way server-to-client progress and rides plain HTTP without extra handshake/reconnect machinery. The frontend animates a token traveling the edge that was actually taken for that email.

## Changes
- `backend/applysync/pipeline/graph.py`: forward per-node stream updates (already computed) into a broadcaster/queue, tagged with `message_id` and `run_id`.
- `backend/applysync/web/sync.py` (or a new `web/sync_stream.py`): `GET /api/sync/stream`, SSE endpoint draining that queue for the duration of an in-progress run.
- `frontend/src/pages/Sync.tsx`: new React Flow canvas alongside the existing 4-stage progress view (kept, not replaced - the aggregate counters are still useful at a glance). `EventSource` subscription drives per-node highlight + edge-token animation.
- New static graph-definition module (frontend) mirroring `pipeline/graph.py`'s node/edge structure, since duplicating that topology by hand risks drift - worth a comment pointing back at the source of truth.

## Benefits
- Makes the escalation-tiering behavior (fast-first-then-escalate-on-failure for extraction vs. escalation-preferred-outright for scrutiny/disambiguation) visible in real time, not just inferable from Langfuse traces after a sync finishes.
- Makes the two distinct rejection paths (cheap pre-LLM `mark_scrutiny_rejected` vs. expensive post-LLM `mark_irrelevant`) visually distinguishable, which matters for judging whether the heuristic pre-filter is pulling its weight on a given sync.
- Complements Langfuse (post-hoc, detailed, per-trace) rather than duplicating it - this stays a live, coarse, in-app view; deep debugging still goes to Langfuse. Tracing remains diagnostic-only per the project's existing rule, and so does this.
- No changes to pipeline logic or routing itself - purely observational, same "never load-bearing" posture as the existing Langfuse integration.

## Known gaps / follow-up
- No automated test coverage plan yet for the SSE endpoint or the frontend event-consumption logic - would need its own pass once built, likely Playwright with a mocked `EventSource`.
- Multiple concurrent browser tabs each opening `/api/sync/stream` would each get their own broadcaster subscription; fine for a single-user tool, not designed for multi-client fan-out.

## Update: Full Audit added as a second tabbed graph
`PipelineGraph.tsx` now renders two graphs behind a tab switch, both static so far: **Sync** (the original scope above) and **Full Audit** (`pipeline/full_audit.py`, see `docs/feasibility/full-audit-rename.md`). They're deliberately drawn as separate node/edge sets (`pipelineNodes`/`pipelineEdges` vs. `fullAuditNodes`/`fullAuditEdges` in `lib/pipelineGraph.ts`), not one graph with a mode flag - the two pipelines share two node factories (`scrutinize_relevance`, `classify_and_extract`) but are structurally different beyond that (full audit never touches `match_existing_application`/`disambiguate_match`/`upsert_db`, and has no escalation-model tiering at all). A new node kind, `suggest` (cyan), marks the `ReviewSuggestion`-queuing terminal nodes distinctly from `write` (emerald, Sync's auto-applied `upsert_db`) - collapsing those two into one color would have erased the exact distinction the full-audit-rename work was about. The live-per-email-animation work described above still applies to both tabs equally once built; no additional backend work was needed for the static graph itself since `fullAuditNodes`/`fullAuditEdges` are hand-authored data, same as the Sync tab.
