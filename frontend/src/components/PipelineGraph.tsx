import { useMemo, useState } from 'react'
import { ReactFlow, Background, MarkerType, useNodesState, type EdgeTypes, type NodeTypes } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import clsx from 'clsx'
import {
  fullAuditEdges,
  fullAuditNodes,
  pipelineEdges,
  pipelineNodes,
  type PipelineEdge,
  type PipelineEdgeRoute,
  type PipelineNode,
} from '../lib/pipelineGraph'
import { useSyncEventStream, type ActiveTransition } from '../lib/useSyncEventStream'
import { AnimatedFlowEdge } from './AnimatedFlowEdge'
import { PipelineFlowNode } from './PipelineFlowNode'

const nodeTypes: NodeTypes = { pipeline: PipelineFlowNode }
const edgeTypes: EdgeTypes = { flow: AnimatedFlowEdge }

// Tailwind can't reach into React Flow's inline SVG stroke, so route colors
// are plain hex here - kept in sync with PipelineFlowNode.tsx's kind colors
// by eye, not programmatically shared (Tailwind's palette isn't available as
// JS values without a separate token file).
const ROUTE_STYLE: Record<PipelineEdgeRoute, { stroke: string; dashed?: boolean; animated?: boolean }> = {
  committed: { stroke: '#6366f1', animated: true },
  reject: { stroke: '#e11d48' },
  agentpath: { stroke: '#7c3aed', animated: true },
  fallopen: { stroke: '#94a3b8', dashed: true },
  noop: { stroke: '#94a3b8', dashed: true },
}

// activeTransition carries BOTH the node an SSE event just arrived at and
// the node that same email was at immediately before (tracked per
// message_id in useSyncEventStream) - so exactly ONE edge fires per email,
// the one it actually traveled, not every edge that happens to end at that
// node. upsert_db alone has three incoming edges (match-resolved,
// match-fallopen, disambiguate-upsert); matching by target alone lit all
// three together on every email that reached upsert_db, including a ball
// that appeared to come from the disambiguation agent on emails that never
// went near it - a real, confirmed bug, not just a documented simplification.
// prevNode === null means this is the first node seen for that email (the
// entry point) - matched by target only, since exactly one edge (from the
// graph's own fetch node) ever leads into an entry node.
//
// Known remaining gap: match-resolved and match-fallopen share the IDENTICAL
// (source, target) pair - match_existing_application -> upsert_db - since
// they're two different conditional branches between the same two nodes.
// Source+target matching genuinely cannot tell them apart; the SSE event
// only says which node fired, not which branch was taken (that would need a
// backend change to publish the routing decision, not just the node name).
// When both tie, this deterministically prefers the non-fallback route
// (committed/agentpath/reject over fallopen/noop) - fallopen only exists
// when the disambiguation agent isn't wired in at all, the rarer case, so
// defaulting to NOT showing it avoids implying that rarer path on a run
// where it's usually the ordinary resolved match that actually happened.
const FALLBACK_ROUTES: PipelineEdgeRoute[] = ['fallopen', 'noop']

function styledEdges(edges: PipelineEdge[], activeTransition: ActiveTransition | null): PipelineEdge[] {
  const rawFiring = edges.map((edge) => {
    const route = edge.data?.route ?? 'committed'
    return {
      edge,
      route,
      firing: Boolean(
        activeTransition &&
          edge.target === activeTransition.node &&
          (activeTransition.prevNode === null || edge.source === activeTransition.prevNode),
      ),
    }
  })

  // Tie-break: within the same (source, target) pair, if a non-fallback
  // route is also firing, suppress the fallback route's own fire.
  const firingIds = new Set(
    rawFiring
      .filter(({ firing }) => firing)
      .filter(({ edge, route }) => {
        if (!FALLBACK_ROUTES.includes(route)) return true
        const hasNonFallbackSibling = rawFiring.some(
          (other) =>
            other.firing &&
            other.edge.id !== edge.id &&
            other.edge.source === edge.source &&
            other.edge.target === edge.target &&
            !FALLBACK_ROUTES.includes(other.route),
        )
        return !hasNonFallbackSibling
      })
      .map(({ edge }) => edge.id),
  )

  return edges.map((edge) => {
    const route = edge.data?.route ?? 'committed'
    const routeStyle = ROUTE_STYLE[route]
    const firing = firingIds.has(edge.id)
    return {
      ...edge,
      type: 'flow',
      animated: false,
      data: { route, firing },
      style: {
        stroke: routeStyle.stroke,
        strokeWidth: 2,
        strokeDasharray: routeStyle.dashed ? '5 5' : undefined,
      },
      // No per-route text color here: a single inline hex can't satisfy WCAG
      // contrast against both the label's light and dark background (the
      // route stroke colors are tuned for a thin SVG line, not a 4.5:1 text
      // ratio on white AND on slate-800). The label div gets a themed
      // Tailwind class instead (AnimatedFlowEdge.tsx) - route color stays on
      // the line/arrowhead only, which isn't a WCAG text-contrast target.
      labelStyle: { fontFamily: 'ui-monospace, monospace', fontSize: 10 },
      markerEnd: { type: MarkerType.ArrowClosed, color: routeStyle.stroke, width: 14, height: 14 },
    }
  })
}

const LEGEND: { label: string; dot: string }[] = [
  { label: 'I/O', dot: 'bg-sky-500' },
  { label: 'Function', dot: 'bg-slate-400' },
  { label: 'LLM call', dot: 'bg-amber-500' },
  { label: 'Agent', dot: 'bg-violet-500' },
  { label: 'Skip / no-op', dot: 'bg-rose-500' },
  { label: 'Write (auto-applied)', dot: 'bg-emerald-500' },
  { label: 'Suggest (queued for review)', dot: 'bg-cyan-500' },
]

type GraphTab = 'sync' | 'audit'

const TABS: { id: GraphTab; label: string; sublabel: string }[] = [
  { id: 'sync', label: 'Sync', sublabel: 'writes directly' },
  { id: 'audit', label: 'Full Audit', sublabel: 'queues for review' },
]

// Owns real drag state (useNodesState + onNodesChange), not just a derived
// array - a node the user drags must stay put across the frequent re-renders
// SSE pulsing causes, not snap back to its authored position on the next
// event. Remounted via `key={tab}` in PipelineGraph below, so switching tabs
// resets positions back to the authored default rather than carrying a drag
// from one graph over to the other.
function PipelineCanvas({
  baseNodes,
  rawEdges,
  activeTransition,
  onSelect,
}: {
  baseNodes: PipelineNode[]
  rawEdges: PipelineEdge[]
  activeTransition: ActiveTransition | null
  onSelect: (node: PipelineNode | null) => void
}) {
  const [nodes, , onNodesChange] = useNodesState(baseNodes)
  const activeNode = activeTransition?.node ?? null

  const displayNodes = useMemo(
    () => nodes.map((node) => ({ ...node, data: { ...node.data, pulsing: node.id === activeNode } })),
    [nodes, activeNode],
  )
  const edges = useMemo(
    () => styledEdges(rawEdges, activeTransition),
    [rawEdges, activeTransition],
  )

  return (
    <ReactFlow
      nodes={displayNodes}
      edges={edges}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      onNodesChange={onNodesChange}
      nodesDraggable
      nodesConnectable={false}
      onNodeClick={(_, node) => onSelect(node as PipelineNode)}
      onPaneClick={() => onSelect(null)}
      panOnScroll
      zoomOnScroll={false}
      fitView
      fitViewOptions={{ padding: 0.12 }}
      proOptions={{ hideAttribution: true }}
    >
      <Background gap={22} size={1} className="!bg-slate-50 dark:!bg-slate-900" />
    </ReactFlow>
  )
}

function DetailPanel({ node }: { node: PipelineNode | null }) {
  if (!node) {
    return (
      <div className="flex h-full items-center justify-center px-6 text-center text-[12px] text-slate-500 dark:text-slate-400">
        Click a node to see what it does, which model tier it uses, and where it lives in the codebase.
      </div>
    )
  }
  const { data } = node
  return (
    <div className="flex h-full flex-col gap-3 overflow-y-auto p-4">
      <div>
        <p className="font-mono text-[10px] uppercase tracking-wide text-slate-500 dark:text-slate-400">
          {data.kindLabel}
        </p>
        <p className="font-mono text-[15px] font-semibold text-slate-900 dark:text-slate-100">{data.title}</p>
      </div>

      {data.note && <p className="text-[12.5px] leading-relaxed text-slate-600 dark:text-slate-300">{data.note}</p>}

      {data.lanes && (
        <div className="space-y-1.5">
          {data.lanes.map((lane) => (
            <div
              key={lane.label}
              className="flex items-center justify-between gap-2 rounded-lg border border-dashed border-slate-200 px-2.5 py-1.5 text-[11px] text-slate-500 dark:border-slate-700 dark:text-slate-400"
            >
              <span>{lane.label}</span>
              <span
                className={clsx(
                  'shrink-0 whitespace-nowrap rounded-full px-1.5 py-0.5 font-mono text-[9.5px]',
                  lane.tier === 'fast'
                    ? 'bg-teal-50 text-teal-700 dark:bg-teal-900/40 dark:text-teal-300'
                    : 'bg-amber-50 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
                )}
              >
                {lane.tierLabel}
              </span>
            </div>
          ))}
        </div>
      )}

      {data.badges && (
        <div className="flex flex-wrap gap-1">
          {data.badges.map((badge) => (
            <span
              key={badge}
              className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-slate-500 dark:bg-slate-700/50 dark:text-slate-400"
            >
              {badge}
            </span>
          ))}
        </div>
      )}

      {data.chips && (
        <div>
          <p className="mb-1 text-[10px] uppercase tracking-wide text-slate-500 dark:text-slate-400">Tools</p>
          <div className="flex flex-wrap gap-1">
            {data.chips.map((chip) => (
              <span
                key={chip.label}
                className={clsx(
                  'rounded-md border px-1.5 py-0.5 font-mono text-[10px]',
                  chip.terminal
                    ? 'border-emerald-300/50 bg-emerald-50 text-emerald-700 dark:border-emerald-700/50 dark:bg-emerald-900/40 dark:text-emerald-300'
                    : 'border-violet-300/50 bg-violet-50 text-violet-700 dark:border-violet-700/50 dark:bg-violet-900/40 dark:text-violet-300',
                )}
              >
                {chip.label}
              </span>
            ))}
          </div>
        </div>
      )}

      <p className="mt-auto pt-2 font-mono text-[10.5px] text-slate-500 dark:text-slate-400">{data.loc}</p>
    </div>
  )
}

export function PipelineGraph() {
  const [tab, setTab] = useState<GraphTab>('sync')
  const baseNodes = tab === 'sync' ? pipelineNodes : fullAuditNodes
  const rawEdges = tab === 'sync' ? pipelineEdges : fullAuditEdges
  const [selected, setSelected] = useState<PipelineNode | null>(null)

  const { activeTransition, lastEvent, connected } = useSyncEventStream()

  return (
    <div
      className="flex flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm dark:border-slate-700 dark:bg-slate-800"
      style={{ height: 620 }}
    >
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-slate-100 px-4 py-2.5 dark:border-slate-700">
        <div className="flex gap-1 rounded-lg bg-slate-100 p-0.5 dark:bg-slate-900">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => {
                setTab(t.id)
                setSelected(null)
              }}
              className={clsx(
                'cursor-pointer rounded-md px-2.5 py-1 text-[11.5px] font-medium transition-colors',
                tab === t.id
                  ? 'bg-white text-slate-900 shadow-sm dark:bg-slate-700 dark:text-slate-100'
                  : 'text-slate-600 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200',
              )}
            >
              {t.label}
              <span className="ml-1.5 text-[10px] text-slate-600 dark:text-slate-400">{t.sublabel}</span>
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2 text-[10.5px] text-slate-500 dark:text-slate-400">
          <span className={clsx('h-1.5 w-1.5 rounded-full', connected ? 'bg-emerald-500' : 'bg-slate-300 dark:bg-slate-600')} />
          {connected
            ? lastEvent
              ? `live — last: ${lastEvent.node}`
              : 'live — waiting for a sync to run'
            : 'connecting…'}
        </div>
      </div>

      <div className="flex shrink-0 flex-wrap gap-x-3 gap-y-1 border-b border-slate-100 px-4 py-2 dark:border-slate-700">
        {LEGEND.map((item) => (
          <span key={item.label} className="flex items-center gap-1.5 text-[11px] text-slate-500 dark:text-slate-400">
            <span className={clsx('h-1.5 w-1.5 rounded-full', item.dot)} />
            {item.label}
          </span>
        ))}
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="flex-1">
          <PipelineCanvas
            key={tab}
            baseNodes={baseNodes}
            rawEdges={rawEdges}
            activeTransition={activeTransition}
            onSelect={setSelected}
          />
        </div>
        <div className="w-[280px] shrink-0 border-l border-slate-100 dark:border-slate-700">
          <DetailPanel node={selected} />
        </div>
      </div>
    </div>
  )
}
