import { useQuery } from '@tanstack/react-query'
import { getDashboard } from '../lib/api'

export function Analytics() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['dashboard', {}],
    queryFn: () => getDashboard({}),
  })

  if (isLoading) {
    return <p className="text-sm text-slate-400" role="status">Loading...</p>
  }
  if (isError || !data) {
    return <p className="text-sm text-rose-500" role="alert">Failed to load analytics.</p>
  }

  return (
    <div>
      <h1 className="mb-4 text-xl font-bold tracking-tight">Response Rate by Platform</h1>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {data.breakdown.map((row) => {
          const rate = row.total > 0 ? Math.floor((100 * row.responded) / row.total) : 0
          return (
            <div key={row.platform} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-700 dark:bg-slate-800">
              <div className="mb-2 flex items-baseline justify-between">
                <span className="text-sm font-semibold capitalize">{row.platform}</span>
                <span className="text-xs text-slate-500 dark:text-slate-400">{row.total} sent</span>
              </div>
              <div className="mb-1.5 h-1.5 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-700">
                <div className="h-full rounded-full bg-brand-500" style={{ width: `${rate}%` }} />
              </div>
              <div className="text-xs text-slate-500 dark:text-slate-400">{rate}% response rate</div>
            </div>
          )
        })}
        {data.breakdown.length === 0 && (
          <p className="text-sm text-slate-500 dark:text-slate-400">No applications yet.</p>
        )}
      </div>
    </div>
  )
}
