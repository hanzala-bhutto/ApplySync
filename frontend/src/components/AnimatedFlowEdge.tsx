import { BaseEdge, EdgeLabelRenderer, getSmoothStepPath, type EdgeProps } from '@xyflow/react'
import type { PipelineEdge } from '../lib/pipelineGraph'

// Same shape as https://reactflow.dev/examples/edges/animating-edges, adapted
// to fire a one-shot traveling dot only when this edge's target node just
// received a real SSE event (PipelineGraph.tsx sets data.firing), instead of
// looping forever - the dot is tied to an actual email moving through the
// pipeline, not decorative ambient motion.
export function AnimatedFlowEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  style,
  markerEnd,
  label,
  labelStyle,
  data,
}: EdgeProps<PipelineEdge>) {
  const [path, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    borderRadius: 12,
  })

  const firing = Boolean(data?.firing)

  return (
    <>
      <BaseEdge id={id} path={path} style={style} markerEnd={markerEnd} />
      {firing && (
        <circle r={4.5} fill={(style?.stroke as string) ?? '#6366f1'} className="motion-reduce:hidden">
          <animateMotion
            dur="0.85s"
            repeatCount="1"
            path={path}
            // Chromium has a known bug where an SMIL animation (animateMotion,
            // animate, etc.) inserted into the DOM dynamically - as React does
            // here, since this <circle> only mounts when `firing` flips true -
            // never actually starts on its own; it sits frozen at its default
            // (cx=0, cy=0) position instead of tracing the path, which is
            // exactly what "the ball is floating off in the corner, not on any
            // edge" looked like. .beginElement() forces it to start.
            //
            // Deferred one frame, not called synchronously in the ref
            // callback: the very first animation of a freshly-loaded page hit
            // the exact same "off the path" symptom even with .beginElement()
            // present, because it fired while React Flow's own initial layout/
            // fitView measurement was still settling (node positions, and so
            // this path's own geometry, weren't final yet). One
            // requestAnimationFrame is enough to land after that settles;
            // every animation after the first was already unaffected, so this
            // is specifically a first-paint race, not a per-edge one.
            ref={(el) => {
              const motionEl = el as SVGAnimateMotionElement | null
              if (!motionEl) return
              requestAnimationFrame(() => motionEl.beginElement?.())
            }}
          />
        </circle>
      )}
      {label && (
        <EdgeLabelRenderer>
          <div
            className="nodrag nopan rounded bg-white/90 px-1 font-mono text-slate-700 dark:bg-slate-800/90 dark:text-slate-200"
            style={{
              position: 'absolute',
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              pointerEvents: 'none',
              ...labelStyle,
            }}
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  )
}
