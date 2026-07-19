import { useCallback, useEffect, useRef, useState } from 'react'

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000'

export interface SyncNodeEvent {
  node: string
  message_id: string
}

// The node this same email was at immediately before `node` - resolved at
// queue-push time from a per-message_id history, not guessed later. This is
// what lets the graph fire the ONE specific edge a given email actually took
// instead of every edge that happens to end at `node`. upsert_db alone has
// three incoming edges (match-resolved, match-fallopen, disambiguate-upsert)
// - without this, all three lit up together whenever ANY email reached
// upsert_db, including a ball appearing to come from the disambiguation
// agent on emails that never went near it. `null` means this is the first
// node seen for this message (the entry point, fetch -> scrutinize).
interface QueuedEvent extends SyncNodeEvent {
  prevNode: string | null
}

export interface ActiveTransition {
  node: string
  prevNode: string | null
}

// Minimum time each event stays the visible "active" node before the next
// queued one is shown - not just a clear-after-idle timeout. Two real
// backend nodes (match_existing_application, upsert_db) are near-instant
// local function calls with no LLM call, so their SSE events can arrive in
// the same JS tick; naive "setActiveNode(event.node) on every message" lets
// React batch both into one render, and the intermediate node's highlight
// never paints at all - it silently "skips". Queueing events and advancing
// one at a time with a guaranteed minimum dwell time fixes that: every node
// gets its own visible turn regardless of how fast the real events arrived.
const MIN_VISIBLE_MS = 900

// Subscribes to GET /api/sync/stream (see web/sync.py + observability.py's
// publish_node_event) and exposes the currently-displayed node/transition,
// advancing through a queue rather than jumping straight to whatever the
// latest SSE message said. Purely observational - matches the backend's own
// "diagnostic, never load-bearing" posture: a dropped/failed connection here
// never affects the sync itself, it just means the graph stops animating.
export function useSyncEventStream() {
  const [activeTransition, setActiveTransition] = useState<ActiveTransition | null>(null)
  const [lastEvent, setLastEvent] = useState<SyncNodeEvent | null>(null)
  const [connected, setConnected] = useState(false)

  const queueRef = useRef<QueuedEvent[]>([])
  const lastNodeByMessage = useRef<Map<string, string>>(new Map())
  const advancingRef = useRef(false)
  const advanceTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const advance = useCallback(() => {
    const next = queueRef.current.shift()
    if (!next) {
      advancingRef.current = false
      setActiveTransition(null)
      return
    }
    advancingRef.current = true
    setLastEvent(next)
    setActiveTransition({ node: next.node, prevNode: next.prevNode })
    advanceTimer.current = setTimeout(advance, MIN_VISIBLE_MS)
  }, [])

  useEffect(() => {
    const source = new EventSource(`${API_BASE}/api/sync/stream`)

    source.onopen = () => setConnected(true)
    source.onerror = () => setConnected(false)
    source.onmessage = (message) => {
      let event: SyncNodeEvent
      try {
        event = JSON.parse(message.data)
      } catch {
        return
      }
      const prevNode = lastNodeByMessage.current.get(event.message_id) ?? null
      lastNodeByMessage.current.set(event.message_id, event.node)
      queueRef.current.push({ ...event, prevNode })
      if (!advancingRef.current) advance()
    }

    return () => {
      if (advanceTimer.current) clearTimeout(advanceTimer.current)
      queueRef.current = []
      lastNodeByMessage.current.clear()
      advancingRef.current = false
      source.close()
    }
  }, [advance])

  return { activeNode: activeTransition?.node ?? null, activeTransition, lastEvent, connected }
}
