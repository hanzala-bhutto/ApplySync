import { Handle, Position, type NodeProps } from '@xyflow/react'
import clsx from 'clsx'
import type { PipelineNode, PipelineNodeKind } from '../lib/pipelineGraph'

// tile text is text-{color}-700 in light mode, not -600: axe caught -600
// against the /15-tint background failing WCAG's 4.5:1 (measured 3.16:1 on
// the emerald tile) - 10px bold text doesn't qualify as "large text" under
// WCAG, so it needs the same 4.5:1 as any other body text, not the 3:1 large-
// text exception.
const KIND_STYLES: Record<PipelineNodeKind, { ring: string; tile: string; glow: string }> = {
  io: {
    ring: 'border-sky-200 dark:border-sky-800',
    tile: 'bg-sky-500/15 text-sky-700 dark:text-sky-300',
    glow: 'ring-sky-400 dark:ring-sky-400 shadow-sky-400/40',
  },
  fn: {
    ring: 'border-slate-200 dark:border-slate-700',
    tile: 'bg-slate-500/15 text-slate-700 dark:text-slate-300',
    glow: 'ring-slate-400 dark:ring-slate-400 shadow-slate-400/40',
  },
  llm: {
    ring: 'border-amber-200 dark:border-amber-800',
    tile: 'bg-amber-500/15 text-amber-700 dark:text-amber-300',
    glow: 'ring-amber-400 dark:ring-amber-400 shadow-amber-400/40',
  },
  agent: {
    ring: 'border-violet-200 dark:border-violet-800',
    tile: 'bg-violet-500/15 text-violet-700 dark:text-violet-300',
    glow: 'ring-violet-400 dark:ring-violet-400 shadow-violet-400/40',
  },
  skip: {
    ring: 'border-rose-200 dark:border-rose-800',
    tile: 'bg-rose-500/15 text-rose-700 dark:text-rose-300',
    glow: 'ring-rose-400 dark:ring-rose-400 shadow-rose-400/40',
  },
  write: {
    ring: 'border-emerald-200 dark:border-emerald-800',
    tile: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300',
    glow: 'ring-emerald-400 dark:ring-emerald-400 shadow-emerald-400/40',
  },
  suggest: {
    ring: 'border-cyan-200 dark:border-cyan-800',
    tile: 'bg-cyan-500/15 text-cyan-700 dark:text-cyan-300',
    glow: 'ring-cyan-400 dark:ring-cyan-400 shadow-cyan-400/40',
  },
}

const HANDLE_POSITION: Record<string, Position> = {
  top: Position.Top,
  bottom: Position.Bottom,
  left: Position.Left,
  right: Position.Right,
}

export function PipelineFlowNode({ data, selected }: NodeProps<PipelineNode>) {
  const style = KIND_STYLES[data.kind]

  return (
    <div
      className={clsx(
        'flex w-[212px] cursor-pointer items-center gap-2.5 rounded-xl border bg-white px-3 py-2.5 shadow-sm transition-all duration-300 hover:shadow-md dark:bg-slate-800',
        style.ring,
        selected && 'ring-2 ring-brand-500 ring-offset-2 ring-offset-white dark:ring-offset-slate-900',
        data.pulsing && ['motion-safe:scale-105', 'shadow-lg', 'ring-2', 'ring-offset-2', 'ring-offset-white', 'dark:ring-offset-slate-900', style.glow],
      )}
    >
      {data.handles.map((h) => (
        <Handle
          key={h.position}
          id={h.position}
          type={h.type}
          position={HANDLE_POSITION[h.position]}
          className="!h-1.5 !w-1.5 !border-none !bg-slate-300 dark:!bg-slate-600"
        />
      ))}

      <span
        className={clsx(
          'flex h-8 w-8 shrink-0 items-center justify-center rounded-lg font-mono text-[10px] font-bold tracking-tight',
          style.tile,
        )}
      >
        {data.monogram}
      </span>
      <div className="min-w-0">
        <p className="truncate font-mono text-[12.5px] font-semibold leading-tight text-slate-900 dark:text-slate-100">
          {data.title}
        </p>
        <p className="truncate text-[10px] leading-tight text-slate-500 dark:text-slate-400">{data.kindLabel}</p>
      </div>
    </div>
  )
}
