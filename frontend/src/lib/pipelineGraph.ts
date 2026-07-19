import type { Edge, Node } from '@xyflow/react'

// Mirrors backend/applysync/pipeline/graph.py + nodes.py exactly - node ids,
// routing, and model-tier claims here must match that file, not the other
// way around. If the graph changes, update this alongside it; don't let this
// drift into an idealized version of the pipeline. See
// docs/feasibility/pipeline-flow-visualization.md for the full rationale.

// 'llm' covers both shapes actually present in this codebase: a function
// with a conditional LLM call (scrutinize_relevance - function is primary),
// and an LLM call with a conditional escalation retry (classify_and_extract
// - LLM is primary, unconditional). Both are "LLM call" nodes, not agents -
// neither has autonomy over what runs next; the code decides that, not the
// model. Only disambiguate_match is an agent (bind_tools + a turn loop the
// model itself steers).
// 'suggest' is deliberately its own kind, not 'write': it queues a
// ReviewSuggestion row for a human to approve, it never writes to
// Application/StatusEvent directly. Conflating it with 'write' (upsert_db's
// auto-applied kind, sync graph only) would erase the exact distinction that
// motivated renaming full_scan to full_audit in the first place - see
// docs/feasibility/full-audit-rename.md.
export type PipelineNodeKind = 'io' | 'fn' | 'llm' | 'agent' | 'skip' | 'write' | 'suggest'

export interface PipelineLane {
  label: string
  tierLabel: string
  tier: 'fast' | 'esc'
}

export type HandleSpec = { position: 'top' | 'bottom' | 'left' | 'right'; type: 'source' | 'target' }

export interface PipelineNodeData extends Record<string, unknown> {
  kind: PipelineNodeKind
  monogram: string
  kindLabel: string
  title: string
  note?: string
  loc: string
  lanes?: PipelineLane[]
  badges?: string[]
  chips?: { label: string; terminal?: boolean }[]
  handles: HandleSpec[]
  // Set client-side from the live SSE stream (useSyncEventStream), not part
  // of the hand-authored graph data - true for ~900ms right after this
  // node's event arrives. See PipelineGraph.tsx.
  pulsing?: boolean
}

export type PipelineNode = Node<PipelineNodeData>

// Top-down: main spine (fetch -> scrutinize -> classify -> match -> upsert)
// runs straight down a fixed x, every branch (reject/skip/agent) peels off to
// the right and either terminates there or rejoins the spine lower down.
//
// Row spacing is deliberately tight (110px, not the ~150px first tried):
// a real bug was found where the taller top-down layout, combined with the
// canvas sitting below the page's ingestion-status card, pushed upsert_db
// below the browser's fold with nothing hinting there was more to scroll to
// - it read as "the DB node disappeared" even though it was always in the
// DOM. Fix is here (compress the graph to fit a shorter canvas) and in
// PipelineGraph.tsx (shorter canvas height) together - see the
// pipeline-flow-visualization feasibility doc for the fuller story.
const SPINE_X = 60
const BRANCH_X = 420

export const pipelineNodes: PipelineNode[] = [
  {
    id: 'fetch_emails',
    type: 'pipeline',
    position: { x: SPINE_X, y: 0 },
    data: {
      kind: 'io',
      monogram: 'IO',
      kindLabel: 'I/O · not a graph node',
      title: 'fetch_emails',
      note: 'Gmail search query, 10-worker concurrent body fetch. Runs once per sync, before the per-email loop.',
      loc: 'gmail/client.py:139',
      handles: [{ position: 'bottom', type: 'source' }],
    },
  },
  {
    id: 'scrutinize_relevance',
    type: 'pipeline',
    position: { x: SPINE_X, y: 110 },
    data: {
      kind: 'llm',
      monogram: 'AI',
      kindLabel: 'function, conditional LLM call · entry point',
      title: 'scrutinize_relevance',
      loc: 'nodes.py:237',
      lanes: [
        { label: 'heuristic — keyword pass/reject', tierLabel: '0 calls', tier: 'fast' },
        { label: 'ambiguous remainder — RelevanceOnlyResult', tierLabel: 'escalation preferred', tier: 'esc' },
      ],
      handles: [
        { position: 'top', type: 'target' },
        { position: 'bottom', type: 'source' },
        { position: 'right', type: 'source' },
      ],
    },
  },
  {
    id: 'mark_scrutiny_rejected',
    type: 'pipeline',
    position: { x: BRANCH_X, y: 110 },
    data: {
      kind: 'skip',
      monogram: 'SK',
      kindLabel: 'function · skip',
      title: 'mark_scrutiny_rejected',
      note: 'Killed before any LLM extraction call — the cheapest reject path. → END',
      loc: 'nodes.py:509',
      handles: [{ position: 'left', type: 'target' }],
    },
  },
  {
    id: 'classify_and_extract',
    type: 'pipeline',
    position: { x: SPINE_X, y: 220 },
    data: {
      kind: 'llm',
      monogram: 'AI',
      kindLabel: 'LLM call, structured output · conditional escalation retry',
      title: 'classify_and_extract',
      loc: 'nodes.py:277',
      lanes: [
        { label: 'fast model — always tried first', tierLabel: '1 call', tier: 'fast' },
        { label: 'on failure / no company — same prompt', tierLabel: 'escalation retry', tier: 'esc' },
      ],
      handles: [
        { position: 'top', type: 'target' },
        { position: 'bottom', type: 'source' },
        { position: 'right', type: 'source' },
      ],
    },
  },
  {
    id: 'mark_irrelevant',
    type: 'pipeline',
    position: { x: BRANCH_X, y: 195 },
    data: {
      kind: 'skip',
      monogram: 'SK',
      kindLabel: 'function · skip',
      title: 'mark_irrelevant',
      note: 'Extraction ran, model said not relevant — the expensive reject path. → END',
      loc: 'nodes.py:509',
      handles: [{ position: 'left', type: 'target' }],
    },
  },
  {
    id: 'mark_extraction_failed',
    type: 'pipeline',
    position: { x: BRANCH_X, y: 280 },
    data: {
      kind: 'skip',
      monogram: 'SK',
      kindLabel: 'function · skip',
      title: 'mark_extraction_failed',
      note: 'Call/parse error, or relevant with no usable company_name even after escalation. → END',
      loc: 'nodes.py:509',
      handles: [{ position: 'left', type: 'target' }],
    },
  },
  {
    id: 'match_existing_application',
    type: 'pipeline',
    position: { x: SPINE_X, y: 330 },
    data: {
      kind: 'fn',
      monogram: 'FN',
      kindLabel: 'function',
      title: 'match_existing_application',
      note: 'No LLM. Fuzzy company match (rapidfuzz) + exact title. Emits candidate_ids instead of guessing when ambiguous.',
      loc: 'nodes.py:361',
      handles: [
        { position: 'top', type: 'target' },
        { position: 'bottom', type: 'source' },
        { position: 'right', type: 'source' },
      ],
    },
  },
  {
    id: 'disambiguate_match',
    type: 'pipeline',
    // y=375, not 330 (aligned with match_existing_application): a real bug
    // was found here - mark_extraction_failed sits at y=280 in this same
    // column, and the ~85px gap every other pair in this column keeps was
    // only 50px here, so the two node cards visually overlapped. 375 keeps
    // this close to match_existing_application's row (330) for a clean
    // connecting edge while restoring real clearance from the node above it.
    position: { x: BRANCH_X, y: 375 },
    data: {
      kind: 'agent',
      monogram: 'AG',
      kindLabel: 'agent · tool loop',
      title: 'disambiguate_match',
      note: 'Hand-rolled loop, not create_react_agent. Only present in the compiled graph when both a Gmail client and a search client are wired in.',
      loc: 'research/disambiguate.py:166',
      badges: ['escalation model preferred', '≤ 8 turns', 'fails open → new_application'],
      chips: [
        { label: 'get_status_history' },
        { label: 'read_source_email' },
        { label: 'web_entity_check' },
        { label: 'submit_verdict', terminal: true },
      ],
      handles: [
        { position: 'left', type: 'target' },
        { position: 'bottom', type: 'source' },
      ],
    },
  },
  {
    id: 'upsert_db',
    type: 'pipeline',
    position: { x: SPINE_X, y: 440 },
    data: {
      kind: 'write',
      monogram: 'DB',
      kindLabel: 'function · write',
      title: 'upsert_db',
      note: 'Creates or updates a row, marks the email processed. → END',
      loc: 'nodes.py:455',
      handles: [
        { position: 'top', type: 'target' },
        { position: 'right', type: 'target' },
      ],
    },
  },
]

export type PipelineEdgeRoute = 'committed' | 'reject' | 'agentpath' | 'fallopen' | 'noop'

export interface PipelineEdgeData extends Record<string, unknown> {
  route: PipelineEdgeRoute
  // Set client-side (PipelineGraph.tsx) from the live SSE stream, not part
  // of the hand-authored graph data - true for ~900ms when this edge's
  // target node just received an event. See AnimatedFlowEdge.tsx.
  firing?: boolean
}

export type PipelineEdge = Edge<PipelineEdgeData>

export const pipelineEdges: PipelineEdge[] = [
  {
    id: 'fetch-scrutinize',
    source: 'fetch_emails',
    sourceHandle: 'bottom',
    target: 'scrutinize_relevance',
    targetHandle: 'top',
    label: 'new_emails',
    data: { route: 'committed' },
  },
  {
    id: 'scrutinize-pass',
    source: 'scrutinize_relevance',
    sourceHandle: 'bottom',
    target: 'classify_and_extract',
    targetHandle: 'top',
    label: 'scrutiny = pass',
    data: { route: 'committed' },
  },
  {
    id: 'scrutinize-reject',
    source: 'scrutinize_relevance',
    sourceHandle: 'right',
    target: 'mark_scrutiny_rejected',
    targetHandle: 'left',
    label: 'reject',
    data: { route: 'reject' },
  },
  {
    id: 'classify-ok',
    source: 'classify_and_extract',
    sourceHandle: 'bottom',
    target: 'match_existing_application',
    targetHandle: 'top',
    label: 'extracted present',
    data: { route: 'committed' },
  },
  {
    id: 'classify-irrelevant',
    source: 'classify_and_extract',
    sourceHandle: 'right',
    target: 'mark_irrelevant',
    targetHandle: 'left',
    label: 'is_relevant = false',
    data: { route: 'reject' },
  },
  {
    id: 'classify-failed',
    source: 'classify_and_extract',
    sourceHandle: 'right',
    target: 'mark_extraction_failed',
    targetHandle: 'left',
    label: 'no result / no company',
    data: { route: 'reject' },
  },
  {
    id: 'match-resolved',
    source: 'match_existing_application',
    sourceHandle: 'bottom',
    target: 'upsert_db',
    targetHandle: 'top',
    label: 'resolved: new / update / duplicate',
    data: { route: 'committed' },
  },
  {
    id: 'match-ambiguous',
    source: 'match_existing_application',
    sourceHandle: 'right',
    target: 'disambiguate_match',
    targetHandle: 'left',
    label: 'candidate_ids set',
    data: { route: 'agentpath' },
  },
  {
    id: 'match-fallopen',
    source: 'match_existing_application',
    sourceHandle: 'right',
    target: 'upsert_db',
    targetHandle: 'right',
    label: 'fall-open (agent not wired)',
    data: { route: 'fallopen' },
  },
  {
    id: 'disambiguate-upsert',
    source: 'disambiguate_match',
    sourceHandle: 'bottom',
    target: 'upsert_db',
    targetHandle: 'right',
    label: 'verdict → MatchDecision',
    data: { route: 'agentpath' },
  },
]

// Mirrors backend/applysync/pipeline/full_audit.py exactly. Deliberately a
// SEPARATE graph, not a variant of pipelineNodes/pipelineEdges above: full
// audit reuses only scrutinize_relevance and classify_and_extract - called
// directly as plain functions, not through a compiled graph - and never
// touches match_existing_application/disambiguate_match/upsert_db. Every
// path here ends in either a ReviewSuggestion row ('suggest' kind) or no
// write at all ('skip' kind); nothing here ever writes to Application/
// StatusEvent directly. See docs/feasibility/full-audit-rename.md.
const AUDIT_SPINE_X = 60
const AUDIT_BRANCH_X = 420

export const fullAuditNodes: PipelineNode[] = [
  {
    id: 'fetch_messages_by_id',
    type: 'pipeline',
    position: { x: AUDIT_SPINE_X, y: 0 },
    data: {
      kind: 'io',
      monogram: 'IO',
      kindLabel: 'I/O · not a graph node',
      title: 'fetch_messages_by_id',
      note: 'Refetches EVERY id in processed_emails, ignoring the idempotency skip a normal sync relies on - this is what makes it "full".',
      loc: 'full_audit.py:191',
      handles: [{ position: 'bottom', type: 'source' }],
    },
  },
  {
    id: 'audit_scrutinize_relevance',
    type: 'pipeline',
    position: { x: AUDIT_SPINE_X, y: 110 },
    data: {
      kind: 'llm',
      monogram: 'AI',
      kindLabel: 'function, conditional LLM call · entry point',
      title: 'scrutinize_relevance',
      note: 'Same node factory as the sync graph, called with no escalation_model - full audit only ever uses the single fast model, no tiering.',
      loc: 'nodes.py:237',
      lanes: [{ label: 'heuristic + ambiguous-case call', tierLabel: 'fast model only', tier: 'fast' }],
      handles: [
        { position: 'top', type: 'target' },
        { position: 'bottom', type: 'source' },
        { position: 'right', type: 'source' },
      ],
    },
  },
  {
    id: 'audit_classify_and_extract',
    type: 'pipeline',
    position: { x: AUDIT_SPINE_X, y: 220 },
    data: {
      kind: 'llm',
      monogram: 'AI',
      kindLabel: 'LLM call, structured output',
      title: 'classify_and_extract',
      note: 'Same node factory as the sync graph, no escalation_model configured here either - no retry tier.',
      loc: 'nodes.py:277',
      lanes: [{ label: 'fast model, single call', tierLabel: 'fast model only', tier: 'fast' }],
      handles: [
        { position: 'top', type: 'target' },
        { position: 'bottom', type: 'source' },
      ],
    },
  },
  {
    id: 'compare_against_stored',
    type: 'pipeline',
    position: { x: AUDIT_SPINE_X, y: 330 },
    data: {
      kind: 'fn',
      monogram: 'FN',
      kindLabel: 'function',
      title: '_application_differs / diff logic',
      note: 'No LLM. Diffs the fresh re-extraction against the SPECIFIC status event this email originally created, not application.current_status - comparing against current_status was the bug behind the 528-suggestion false-positive flood.',
      loc: 'full_audit.py:20',
      handles: [
        { position: 'top', type: 'target' },
        { position: 'right', type: 'target' },
        { position: 'bottom', type: 'source' },
      ],
    },
  },
  {
    id: 'queue_new_application',
    type: 'pipeline',
    position: { x: AUDIT_SPINE_X, y: 440 },
    data: {
      kind: 'suggest',
      monogram: 'RS',
      kindLabel: 'function · suggest',
      title: 'ReviewSuggestion(new_application)',
      note: 'No prior application on record for this email at all. Queued for human approval, nothing auto-created.',
      loc: 'full_audit.py:144',
      handles: [{ position: 'top', type: 'target' }],
    },
  },
  {
    id: 'queue_update_existing',
    type: 'pipeline',
    position: { x: AUDIT_BRANCH_X, y: 440 },
    data: {
      kind: 'suggest',
      monogram: 'RS',
      kindLabel: 'function · suggest',
      title: 'ReviewSuggestion(update_existing)',
      note: 'Re-extraction disagrees with what this email originally recorded, or matches an existing application by company+title. Queued, not applied.',
      loc: 'full_audit.py:127',
      handles: [{ position: 'top', type: 'target' }],
    },
  },
  {
    id: 'queue_reclassify_irrelevant',
    type: 'pipeline',
    position: { x: AUDIT_BRANCH_X + 360, y: 440 },
    data: {
      kind: 'suggest',
      monogram: 'RS',
      kindLabel: 'function · suggest',
      title: 'ReviewSuggestion(reclassify_irrelevant)',
      note: 'An application exists on record for this email, but today\'s pipeline no longer thinks it\'s relevant. Queued, never auto-deletes the application.',
      loc: 'full_audit.py:117',
      handles: [{ position: 'top', type: 'target' }],
    },
  },
  {
    id: 'no_suggestion',
    type: 'pipeline',
    position: { x: AUDIT_BRANCH_X + 720, y: 440 },
    data: {
      kind: 'skip',
      monogram: 'SK',
      kindLabel: 'function · no-op',
      title: 'no suggestion created',
      note: 'Re-extraction agrees with what\'s stored, a suggestion is already pending for this email, or it was never relevant either time. Nothing written.',
      loc: 'full_audit.py:113',
      handles: [{ position: 'top', type: 'target' }],
    },
  },
]

export const fullAuditEdges: PipelineEdge[] = [
  {
    id: 'audit-fetch-scrutinize',
    source: 'fetch_messages_by_id',
    sourceHandle: 'bottom',
    target: 'audit_scrutinize_relevance',
    targetHandle: 'top',
    label: 'every processed_emails id',
    data: { route: 'committed' },
  },
  {
    id: 'audit-scrutinize-pass',
    source: 'audit_scrutinize_relevance',
    sourceHandle: 'bottom',
    target: 'audit_classify_and_extract',
    targetHandle: 'top',
    label: 'scrutiny = pass',
    data: { route: 'committed' },
  },
  {
    id: 'audit-scrutinize-reject',
    source: 'audit_scrutinize_relevance',
    sourceHandle: 'right',
    target: 'compare_against_stored',
    targetHandle: 'right',
    label: 'scrutiny = reject → new_extracted = None (classify never called)',
    data: { route: 'reject' },
  },
  {
    id: 'audit-classify-compare',
    source: 'audit_classify_and_extract',
    sourceHandle: 'bottom',
    target: 'compare_against_stored',
    targetHandle: 'top',
    label: 'extracted (or None if irrelevant/failed)',
    data: { route: 'committed' },
  },
  {
    id: 'compare-new',
    source: 'compare_against_stored',
    sourceHandle: 'bottom',
    target: 'queue_new_application',
    targetHandle: 'top',
    label: 'no old application, extracted present',
    data: { route: 'committed' },
  },
  {
    id: 'compare-update',
    source: 'compare_against_stored',
    sourceHandle: 'bottom',
    target: 'queue_update_existing',
    targetHandle: 'top',
    label: 'differs from stored',
    data: { route: 'committed' },
  },
  {
    id: 'compare-irrelevant',
    source: 'compare_against_stored',
    sourceHandle: 'bottom',
    target: 'queue_reclassify_irrelevant',
    targetHandle: 'top',
    label: 'old application existed, now extracted = None',
    data: { route: 'committed' },
  },
  {
    id: 'compare-noop',
    source: 'compare_against_stored',
    sourceHandle: 'bottom',
    target: 'no_suggestion',
    targetHandle: 'top',
    label: 'agrees, already pending, or still irrelevant',
    data: { route: 'noop' },
  },
]
