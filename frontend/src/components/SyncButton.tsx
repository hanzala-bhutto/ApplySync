import { useEffect, useRef } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getSyncStatus, postStopSync, postSync } from '../lib/api'
import { useToast } from '../lib/toast'

function formatLastSynced(finishedAt: string | null): string {
  if (!finishedAt) return 'Never synced'
  return `Synced ${new Date(finishedAt).toLocaleString()}`
}

export function SyncButton() {
  const { showToast } = useToast()
  const queryClient = useQueryClient()
  const wasInProgress = useRef(false)

  const { data } = useQuery({
    queryKey: ['sync-status'],
    queryFn: getSyncStatus,
    // Only poll while a sync is actually running - no point hammering the
    // endpoint the rest of the time.
    refetchInterval: (query) => (query.state.data?.in_progress ? 1500 : false),
  })

  const mutation = useMutation({
    mutationFn: postSync,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['sync-status'] }),
    onError: (error: Error) => {
      const message = error.message.includes('409')
        ? 'A sync is already in progress.'
        : 'Could not start sync.'
      showToast({ message, variant: 'error' })
    },
  })

  const stopMutation = useMutation({
    mutationFn: postStopSync,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['sync-status'] }),
    onError: () => showToast({ message: 'Could not stop the sync.', variant: 'error' }),
  })

  useEffect(() => {
    if (!data) return
    if (wasInProgress.current && !data.in_progress) {
      const cancelled = data.latest_run?.errors === 'cancelled_by_user'
      if (cancelled) {
        showToast({ message: 'Sync stopped.', variant: 'success' })
      } else if (data.last_error) {
        // Plain-language message, not the raw backend exception text (same
        // rule as every other mutation's error toast in this app) - the
        // detail is still in data.last_error for anyone checking the server
        // terminal, just not surfaced verbatim to the user here.
        showToast({ message: 'Sync failed. Check the server terminal for details.', variant: 'error' })
      } else if (data.latest_run) {
        const run = data.latest_run
        showToast({
          message: `Synced: ${run.applications_created} new, ${run.events_created} updates from ${run.emails_relevant} relevant emails.`,
          variant: 'success',
        })
      }
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      queryClient.invalidateQueries({ queryKey: ['reminders'] })
    }
    wasInProgress.current = data.in_progress
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data?.in_progress])

  const inProgress = data?.in_progress ?? false

  return (
    <div className="flex items-center gap-2">
      <span className="hidden text-xs text-slate-500 sm:inline dark:text-slate-400">
        {inProgress ? (data?.stopping ? 'Stopping...' : 'Syncing...') : formatLastSynced(data?.latest_run?.finished_at ?? null)}
      </span>
      {inProgress && (
        <button
          type="button"
          onClick={() => stopMutation.mutate()}
          disabled={data?.stopping || stopMutation.isPending}
          className="cursor-pointer rounded-lg border border-rose-200 px-2.5 py-1.5 text-xs font-medium text-rose-600 transition-colors hover:bg-rose-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-rose-800 dark:text-rose-400 dark:hover:bg-rose-950/40"
        >
          {data?.stopping ? 'Stopping…' : 'Stop'}
        </button>
      )}
      <button
        type="button"
        onClick={() => mutation.mutate()}
        disabled={inProgress || mutation.isPending}
        className="cursor-pointer rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs font-medium transition-colors hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-700 dark:hover:bg-slate-800"
      >
        {inProgress ? 'Syncing...' : 'Sync Now'}
      </button>
    </div>
  )
}
