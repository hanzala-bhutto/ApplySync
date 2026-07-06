import { useQuery } from '@tanstack/react-query'
import { getSyncStatus, type PipelineRun } from '../lib/api'
import { SyncButton } from '../components/SyncButton'

function StageBar({ label, count, total }: { label: string; count: number; total: number | null }) {
  const pct = total && total > 0 ? Math.min(100, Math.floor((100 * count) / total)) : 0
  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between text-sm">
        <span className="font-medium">{label}</span>
        <span className="text-slate-500 dark:text-slate-400">
          {count} / {total ?? '?'}
        </span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-700">
        <div className="h-full rounded-full bg-brand-500 transition-all" style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

function runStatusLabel(run: PipelineRun): string {
  if (!run.finished_at) return 'In progress'
  if (run.errors) return 'Failed'
  return 'Completed'
}

export function Sync() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['sync-status'],
    queryFn: getSyncStatus,
    refetchInterval: 2000,
  })

  if (isLoading) {
    return (
      <p className="text-sm text-slate-400" role="status">
        Loading...
      </p>
    )
  }
  if (isError || !data) {
    return (
      <p className="text-sm text-rose-500" role="alert">
        Failed to load sync status.
      </p>
    )
  }

  const run = data.latest_run

  return (
    <div>
      <div className="mb-4 flex items-baseline justify-between">
        <h1 className="text-xl font-bold tracking-tight">Sync</h1>
        <SyncButton />
      </div>

      <div
        aria-live="polite"
        className="mb-6 rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-700 dark:bg-slate-800"
      >
        {!run ? (
          <p className="text-sm text-slate-500 dark:text-slate-400">No sync has run yet.</p>
        ) : (
          <div className="space-y-4">
            <div className="flex items-baseline justify-between text-sm">
              <span className="font-medium">Ingestion</span>
              <span className="text-slate-500 dark:text-slate-400">
                {run.emails_total === null ? 'Fetching emails...' : `${run.emails_total} emails found`}
              </span>
            </div>
            <StageBar label="Scrutiny" count={run.emails_scrutinized} total={run.emails_total} />
            <StageBar label="Extraction" count={run.emails_extracted} total={run.emails_total} />
            <StageBar label="Classification / DB Write" count={run.emails_written} total={run.emails_total} />
            <p className="text-xs text-slate-500 dark:text-slate-400">
              {data.in_progress
                ? 'Sync in progress...'
                : run.errors
                  ? 'Last sync failed. Check the server terminal for details.'
                  : `Last sync finished: ${run.applications_created} new, ${run.events_created} updates from ${run.emails_relevant} relevant emails.`}
            </p>
          </div>
        )}
      </div>

      <h2 className="mb-2 text-sm font-semibold text-slate-500 dark:text-slate-400">Recent runs</h2>
      {data.history.length === 0 ? (
        <p className="rounded-xl border border-dashed border-slate-200 p-6 text-center text-sm text-slate-500 dark:border-slate-700 dark:text-slate-400">
          No runs yet.
        </p>
      ) : (
        <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm dark:border-slate-700 dark:bg-slate-800">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500 dark:bg-slate-700/50 dark:text-slate-400">
              <tr>
                <th className="px-4 py-2">Started</th>
                <th className="px-4 py-2">Status</th>
                <th className="px-4 py-2">Emails</th>
                <th className="px-4 py-2">Applications</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-700">
              {data.history.map((historicRun) => (
                <tr key={historicRun.id}>
                  <td className="px-4 py-2">{new Date(historicRun.started_at).toLocaleString()}</td>
                  <td className="px-4 py-2">{runStatusLabel(historicRun)}</td>
                  <td className="px-4 py-2 text-slate-500 dark:text-slate-400">{historicRun.emails_fetched}</td>
                  <td className="px-4 py-2 text-slate-500 dark:text-slate-400">
                    {historicRun.applications_created}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
