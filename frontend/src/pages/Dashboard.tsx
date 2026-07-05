import { useSearchParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient, keepPreviousData } from '@tanstack/react-query'
import {
  DndContext,
  useDraggable,
  useDroppable,
  PointerSensor,
  KeyboardSensor,
  useSensor,
  useSensors,
  closestCenter,
  type DragEndEvent,
} from '@dnd-kit/core'
import { CSS } from '@dnd-kit/utilities'
import { motion } from 'framer-motion'
import { getDashboard, patchStatus, type Application, type DashboardFilters, type DashboardResponse } from '../lib/api'
import { avatarFor } from '../lib/avatar'
import { statusStyle } from '../lib/status'
import { useToast } from '../lib/toast'

export function Dashboard() {
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { showToast } = useToast()

  const filters: DashboardFilters = {
    year: searchParams.get('year') ?? undefined,
    platform: searchParams.get('platform') ?? undefined,
    company: searchParams.get('company') ?? undefined,
    status: searchParams.get('status') ?? undefined,
  }
  const queryKey = ['dashboard', filters]

  const { data, isLoading, isError } = useQuery({
    queryKey,
    queryFn: () => getDashboard(filters),
    // Every filter change is a new queryKey. Without this, React Query
    // unmounts everything back to the isLoading state on each change instead
    // of keeping the current board visible while the new one loads - which
    // reads as "the whole page refreshing" even though it's just a refetch.
    placeholderData: keepPreviousData,
  })

  const statusMutation = useMutation({
    mutationFn: ({ id, status }: { id: number; status: string }) => patchStatus(id, status),
    onMutate: async ({ id, status }) => {
      await queryClient.cancelQueries({ queryKey })
      const previous = queryClient.getQueryData<DashboardResponse>(queryKey)
      if (previous) {
        queryClient.setQueryData<DashboardResponse>(queryKey, (old) => moveApplication(old, id, status))
      }
      return { previous }
    },
    onError: (_err, _vars, context) => {
      if (context?.previous) queryClient.setQueryData(queryKey, context.previous)
      showToast({ message: 'Could not update status. Reverted.', variant: 'error' })
    },
  })

  function moveApplication(board: DashboardResponse | undefined, id: number, newStatus: string): DashboardResponse | undefined {
    if (!board) return board
    let moved: Application | undefined
    const nextBoard: Record<string, Application[]> = {}
    for (const [status, apps] of Object.entries(board.board)) {
      nextBoard[status] = apps.filter((a) => {
        if (a.id === id) {
          moved = { ...a, current_status: newStatus }
          return false
        }
        return true
      })
    }
    if (!moved) return board
    nextBoard[newStatus] = [...(nextBoard[newStatus] ?? []), moved]
    return { ...board, board: nextBoard }
  }

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event
    if (!over) return
    const applicationId = active.data.current?.applicationId as number | undefined
    const oldStatus = active.data.current?.currentStatus as string | undefined
    const newStatus = over.data.current?.status as string | undefined
    const companyName = active.data.current?.companyName as string | undefined
    if (!applicationId || !newStatus || !oldStatus || newStatus === oldStatus) return

    statusMutation.mutate(
      { id: applicationId, status: newStatus },
      {
        onSuccess: () => {
          showToast({
            message: `Moved ${companyName ?? 'application'} to ${newStatus}`,
            variant: 'success',
            action: {
              label: 'Undo',
              onClick: () => statusMutation.mutate({ id: applicationId, status: oldStatus }),
            },
          })
        },
      }
    )
  }

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
    // dnd-kit's default binds BOTH Space and Enter to start/end a drag,
    // which would fight with Enter opening the card (the button's native
    // keyboard behavior). Restrict drag activation to Space only, so Enter
    // stays free to just click through.
    useSensor(KeyboardSensor, {
      keyboardCodes: { start: ['Space'], cancel: ['Escape'], end: ['Space'] },
    })
  )

  function updateFilter(key: string, value: string) {
    const next = new URLSearchParams(searchParams)
    if (value) next.set(key, value)
    else next.delete(key)
    setSearchParams(next)
  }

  const hasActiveFilters = Boolean(filters.year || filters.platform || filters.company || filters.status)

  if (isLoading) {
    return <p className="text-sm text-slate-400" role="status">Loading...</p>
  }
  if (isError || !data) {
    return <p className="text-sm text-rose-500" role="alert">Failed to load dashboard.</p>
  }

  return (
    <div>
      <form
        className="mb-6 flex flex-wrap items-center gap-2 rounded-xl border border-slate-200 bg-white p-3 shadow-sm dark:border-slate-800 dark:bg-slate-900"
        onSubmit={(e) => e.preventDefault()}
      >
        <div className="relative min-w-[180px] flex-1">
          <svg
            className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
            aria-hidden="true"
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-4.35-4.35m0 0A7.5 7.5 0 104.5 4.5a7.5 7.5 0 0012.15 12.15z" />
          </svg>
          <label htmlFor="company-search" className="sr-only">Search company</label>
          <input
            id="company-search"
            type="text"
            defaultValue={filters.company ?? ''}
            placeholder="Search company..."
            onChange={(e) => updateFilter('company', e.target.value)}
            className="w-full rounded-lg border border-slate-200 bg-slate-50 py-1.5 pl-8 pr-3 text-sm placeholder:text-slate-400 focus:border-brand-400 focus:bg-white focus:outline-none focus:ring-2 focus:ring-brand-100 dark:border-slate-700 dark:bg-slate-800 dark:focus:bg-slate-800 dark:focus:ring-brand-900"
          />
        </div>
        <label htmlFor="year-filter" className="sr-only">Filter by year</label>
        <select
          id="year-filter"
          value={filters.year ?? ''}
          onChange={(e) => updateFilter('year', e.target.value)}
          className="rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-sm focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-100 dark:border-slate-700 dark:bg-slate-800"
        >
          <option value="">All years</option>
          {data.filter_options.years.map((y) => (
            <option key={y} value={y}>{y}</option>
          ))}
        </select>
        <label htmlFor="platform-filter" className="sr-only">Filter by platform</label>
        <select
          id="platform-filter"
          value={filters.platform ?? ''}
          onChange={(e) => updateFilter('platform', e.target.value)}
          className="rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-sm focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-100 dark:border-slate-700 dark:bg-slate-800"
        >
          <option value="">All platforms</option>
          {data.filter_options.platforms.map((p) => (
            <option key={p} value={p}>{p}</option>
          ))}
        </select>
        <label htmlFor="status-filter" className="sr-only">Filter by status</label>
        <select
          id="status-filter"
          value={filters.status ?? ''}
          onChange={(e) => updateFilter('status', e.target.value)}
          className="rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-sm capitalize focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-100 dark:border-slate-700 dark:bg-slate-800"
        >
          <option value="">All statuses</option>
          {data.filter_options.statuses.map((s) => (
            <option key={s} value={s} className="capitalize">{s}</option>
          ))}
        </select>
        {hasActiveFilters && (
          <button
            type="button"
            onClick={() => setSearchParams(new URLSearchParams())}
            className="rounded-lg px-2 py-1.5 text-sm font-medium text-brand-600 hover:bg-brand-50 dark:text-brand-400 dark:hover:bg-brand-950"
          >
            Clear
          </button>
        )}
      </form>

      <section className="mb-10">
        <div className="mb-4 flex items-baseline justify-between">
          <h1 className="text-xl font-bold tracking-tight">Pipeline</h1>
          <span className="text-xs text-slate-500 dark:text-slate-400">
            drag a card to correct its status (or focus it and press Space)
          </span>
        </div>
        <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
          <div className="flex gap-4 overflow-x-auto pb-2">
            {data.status_order.map((status) => (
              <KanbanColumn key={status} status={status} applications={data.board[status] ?? []} onOpen={(id) => navigate(`/applications/${id}`)} />
            ))}
          </div>
        </DndContext>
      </section>

      {data.reminders.length > 0 && (
        <section className="mb-10">
          <h2 className="mb-3 text-sm font-semibold text-slate-500 dark:text-slate-400">Follow-up reminders</h2>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {data.reminders.map((application) => (
              <button
                key={application.id}
                onClick={() => navigate(`/applications/${application.id}`)}
                className="flex items-center gap-2.5 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2.5 text-left transition-colors hover:bg-amber-100 dark:border-amber-900 dark:bg-amber-950/40 dark:hover:bg-amber-950/70"
              >
                <svg className="h-4 w-4 shrink-0 text-amber-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden="true">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
                </svg>
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium">{application.company_name}</div>
                  <div className="truncate text-xs text-slate-500 dark:text-slate-400">
                    applied {application.applied_date}, no update since
                  </div>
                </div>
              </button>
            ))}
          </div>
        </section>
      )}

      <section>
        <h2 className="mb-3 text-sm font-semibold text-slate-500 dark:text-slate-400">By platform</h2>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {data.breakdown.map((row) => {
            const rate = row.total > 0 ? Math.floor((100 * row.responded) / row.total) : 0
            return (
              <div key={row.platform} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
                <div className="mb-2 flex items-baseline justify-between">
                  <span className="text-sm font-semibold capitalize">{row.platform}</span>
                  <span className="text-xs text-slate-500 dark:text-slate-400">{row.total} sent</span>
                </div>
                <div className="mb-1.5 h-1.5 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                  <div className="h-full rounded-full bg-brand-500" style={{ width: `${rate}%` }} />
                </div>
                <div className="text-xs text-slate-500 dark:text-slate-400">{rate}% response rate</div>
              </div>
            )
          })}
        </div>
      </section>
    </div>
  )
}

function KanbanColumn({
  status,
  applications,
  onOpen,
}: {
  status: string
  applications: Application[]
  onOpen: (id: number) => void
}) {
  const style = statusStyle(status)
  const { setNodeRef, isOver } = useDroppable({ id: `column-${status}`, data: { status } })

  return (
    <div className="flex max-h-[75vh] w-72 shrink-0 flex-col rounded-xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex shrink-0 items-center gap-2 border-b border-slate-100 px-3.5 py-3 dark:border-slate-800">
        <span className={`h-2 w-2 rounded-full ${style.dot}`} aria-hidden="true" />
        <span className="text-sm font-semibold capitalize">{status}</span>
        <span className="ml-auto rounded-full bg-slate-100 px-1.5 py-0.5 text-xs font-medium text-slate-500 dark:bg-slate-800 dark:text-slate-400">
          {applications.length}
        </span>
      </div>
      <div
        ref={setNodeRef}
        className={`min-h-16 flex-1 space-y-2 overflow-y-auto p-2.5 transition-colors ${isOver ? 'bg-brand-50 dark:bg-brand-950/40' : ''}`}
      >
        {applications.map((application) => (
          <KanbanCard key={application.id} application={application} onOpen={onOpen} />
        ))}
        {applications.length === 0 && (
          <div className="flex h-16 items-center justify-center rounded-lg border border-dashed border-slate-200 text-xs text-slate-500 dark:border-slate-800 dark:text-slate-500">
            Empty
          </div>
        )}
      </div>
    </div>
  )
}

function KanbanCard({ application, onOpen }: { application: Application; onOpen: (id: number) => void }) {
  const av = avatarFor(application.company_name)
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({
    id: `application-${application.id}`,
    data: {
      applicationId: application.id,
      currentStatus: application.current_status,
      companyName: application.company_name,
    },
  })

  return (
    <motion.div layout transition={{ duration: 0.2 }}>
      <button
        ref={setNodeRef}
        {...listeners}
        {...attributes}
        onClick={() => onOpen(application.id)}
        aria-label={`${application.company_name}, ${application.job_title}, status ${application.current_status}. Press Enter to open, Space to move.`}
        style={{
          transform: transform ? CSS.Translate.toString(transform) : undefined,
          opacity: isDragging ? 0.5 : 1,
        }}
        className="block w-full cursor-grab rounded-lg border border-slate-100 bg-white p-3 text-left shadow-sm transition-shadow hover:shadow-md focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 active:cursor-grabbing dark:border-slate-800 dark:bg-slate-800/60"
      >
        <div className="flex items-start gap-2.5">
          <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full ${av.bg} text-xs font-bold text-white`}>
            {av.initial}
          </span>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-semibold leading-tight">{application.company_name}</div>
            <div className="truncate text-xs leading-tight text-slate-500 dark:text-slate-400">{application.job_title}</div>
            <div
              className={`mt-1.5 inline-block rounded px-1.5 py-0.5 text-[10px] font-medium capitalize ${statusStyle(application.current_status).bg} ${statusStyle(application.current_status).text}`}
            >
              {application.platform}
            </div>
          </div>
        </div>
      </button>
    </motion.div>
  )
}
