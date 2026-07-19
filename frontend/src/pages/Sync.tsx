import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getSyncStatus, postFullAudit, postStopSync, type PipelineRun } from '../lib/api'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { PipelineGraph } from '../components/PipelineGraph'
import { useToast } from '../lib/toast'

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
  if (run.errors === 'cancelled_by_user') return 'Stopped'
  if (run.errors) return 'Failed'
  return 'Completed'
}

// 'full_scan' is the pre-rename value still held by older stored runs (see
// docs/feasibility/full-audit-rename.md) - treated the same as 'full_audit'.
function runTypeLabel(runType: string): string {
  return runType === 'full_audit' || runType === 'full_scan' ? 'Full Audit' : 'Sync'
}

function FinishedSummary({ run }: { run: PipelineRun }) {
  if (run.run_type === 'full_audit' || run.run_type === 'full_scan') {
    // A full audit never creates applications/events directly - it only
    // queues suggestions, so the number that actually answers "did this
    // find anything" is suggestions_created, not applications/events counts
    // (which are always 0 by design and would otherwise read as "nothing
    // happened" even when there's real work waiting on the Review page).
    if (run.suggestions_created === 0) {
      return <>Full audit finished: {run.emails_relevant} relevant emails re-checked, nothing needed review.</>
    }
    return (
      <>
        Full audit finished: {run.suggestions_created} suggestion{run.suggestions_created === 1 ? '' : 's'} queued
        for <Link to="/review" className="underline underline-offset-2 hover:no-underline">review</Link>, out of{' '}
        {run.emails_relevant} relevant emails.
      </>
    )
  }
  return (
    <>
      Last sync finished: {run.applications_created} new, {run.events_created} updates from {run.emails_relevant}{' '}
      relevant emails.
    </>
  )
}

export function Sync() {
  const { showToast } = useToast()
  const queryClient = useQueryClient()
  const [confirmFullAudit, setConfirmFullAudit] = useState(false)

  const { data, isLoading, isError } = useQuery({
    queryKey: ['sync-status'],
    queryFn: getSyncStatus,
    refetchInterval: 2000,
  })

  const fullAuditMutation = useMutation({
    mutationFn: postFullAudit,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['sync-status'] }),
    onError: (error: Error) => {
      const message = error.message.includes('409')
        ? 'A sync or full audit is already in progress.'
        : 'Could not start full audit.'
      showToast({ message, variant: 'error' })
    },
  })

  const stopMutation = useMutation({
    mutationFn: postStopSync,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sync-status'] })
      showToast({ message: 'Stopping after the current email finishes...', variant: 'success' })
    },
    onError: () => {
      showToast({ message: 'Could not stop the sync - it may have already finished.', variant: 'error' })
    },
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
  const runTypeInProgress = data.in_progress ? data.current_run_type : null

  return (
    <div>
      <h1 className="mb-4 text-xl font-bold tracking-tight">Sync</h1>

      <div
        aria-live="polite"
        className="mb-6 rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-700 dark:bg-slate-800"
      >
        {!run ? (
          <p className="text-sm text-slate-500 dark:text-slate-400">No sync has run yet.</p>
        ) : (
          <div className="space-y-4">
            <div className="flex items-baseline justify-between text-sm">
              <span className="font-medium">
                {runTypeInProgress ? `${runTypeLabel(runTypeInProgress)}: Ingestion` : 'Ingestion'}
              </span>
              <div className="flex items-center gap-3">
                <span className="text-slate-500 dark:text-slate-400">
                  {run.emails_total === null ? 'Fetching emails...' : `${run.emails_total} emails found`}
                </span>
                {data.in_progress && (
                  <button
                    type="button"
                    onClick={() => stopMutation.mutate()}
                    disabled={data.stopping || stopMutation.isPending}
                    className="cursor-pointer rounded-lg border border-rose-200 px-2.5 py-1 text-xs font-medium text-rose-600 transition-colors hover:bg-rose-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-rose-800 dark:text-rose-400 dark:hover:bg-rose-950/40"
                  >
                    {data.stopping ? 'Stopping…' : 'Stop'}
                  </button>
                )}
              </div>
            </div>
            <StageBar label="Scrutiny" count={run.emails_scrutinized} total={run.emails_total} />
            <StageBar label="Extraction" count={run.emails_extracted} total={run.emails_total} />
            <StageBar label="Classification / DB Write" count={run.emails_written} total={run.emails_total} />
            <p className="text-xs text-slate-500 dark:text-slate-400">
              {data.in_progress
                ? data.stopping
                  ? 'Stopping after the current email finishes...'
                  : `${runTypeLabel(data.current_run_type ?? 'incremental')} in progress...`
                : run.errors === 'cancelled_by_user'
                  ? `Stopped: ${run.emails_written} of ${run.emails_total ?? '?'} emails processed before you stopped it.`
                  : run.errors
                    ? 'Last sync failed. Check the server terminal for details.'
                    : <FinishedSummary run={run} />}
            </p>
          </div>
        )}
      </div>

      <h2 className="mb-2 text-sm font-semibold text-slate-500 dark:text-slate-400">Pipeline</h2>
      <div className="mb-6">
        <PipelineGraph />
      </div>

      <div className="mb-6 rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-700 dark:bg-slate-800">
        <div className="mb-1 flex items-baseline justify-between">
          <h2 className="text-sm font-semibold">Full Audit</h2>
          <button
            type="button"
            onClick={() => setConfirmFullAudit(true)}
            disabled={data.in_progress || fullAuditMutation.isPending}
            className="cursor-pointer rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs font-medium transition-colors hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-700 dark:hover:bg-slate-800"
          >
            Run Full Audit
          </button>
        </div>
        <p className="text-xs text-slate-500 dark:text-slate-400">
          Re-checks every email ever seen against today's pipeline, not just new ones - useful after a
          prompt or filter change. Slower than a normal sync, and any disagreement with what's already
          stored is queued on the Review page rather than applied automatically.
        </p>
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
                <th className="px-4 py-2">Type</th>
                <th className="px-4 py-2">Status</th>
                <th className="px-4 py-2">Emails</th>
                <th className="px-4 py-2">Applications</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-700">
              {data.history.map((historicRun) => (
                <tr key={historicRun.id}>
                  <td className="px-4 py-2">{new Date(historicRun.started_at).toLocaleString()}</td>
                  <td className="px-4 py-2 text-slate-500 dark:text-slate-400">
                    {runTypeLabel(historicRun.run_type)}
                  </td>
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

      <ConfirmDialog
        open={confirmFullAudit}
        title="Run a full audit?"
        description="This re-checks every email ever seen against today's pipeline. It can take a while and any disagreement with existing data is queued for your review, not applied automatically."
        confirmLabel="Run Full Audit"
        onCancel={() => setConfirmFullAudit(false)}
        onConfirm={() => {
          setConfirmFullAudit(false)
          fullAuditMutation.mutate()
        }}
      />
    </div>
  )
}
