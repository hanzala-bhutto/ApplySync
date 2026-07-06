import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getReviewSuggestions, postApproveSuggestion, postRejectSuggestion, type ReviewSuggestion } from '../lib/api'
import { useToast } from '../lib/toast'

interface ExtractSnapshot {
  company_name?: string | null
  job_title?: string | null
  status?: string | null
}

function parseSnapshot(json: string | null): ExtractSnapshot | null {
  if (!json) return null
  try {
    return JSON.parse(json) as ExtractSnapshot
  } catch {
    return null
  }
}

function actionLabel(action: ReviewSuggestion['action']): string {
  switch (action) {
    case 'new_application':
      return 'New application'
    case 'update_existing':
      return 'Update existing application'
    case 'reclassify_irrelevant':
      return 'No longer looks relevant'
    default:
      return action
  }
}

function DiffRow({ label, before, after }: { label: string; before?: string | null; after?: string | null }) {
  const hasBefore = before !== undefined && before !== null
  if (hasBefore && before === after) return null
  return (
    <div className="flex items-baseline justify-between gap-3 text-sm">
      <span className="text-slate-500 dark:text-slate-400">{label}</span>
      <span>
        {hasBefore && <span className="mr-1.5 text-slate-400 line-through dark:text-slate-500">{before}</span>}
        <span className="font-medium">{after ?? '—'}</span>
      </span>
    </div>
  )
}

export function Review() {
  const { showToast } = useToast()
  const queryClient = useQueryClient()

  const { data, isLoading, isError } = useQuery({
    queryKey: ['review-suggestions'],
    queryFn: getReviewSuggestions,
  })

  function invalidate() {
    queryClient.invalidateQueries({ queryKey: ['review-suggestions'] })
    queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    queryClient.invalidateQueries({ queryKey: ['reminders'] })
  }

  const approveMutation = useMutation({
    mutationFn: postApproveSuggestion,
    onSuccess: () => {
      invalidate()
      showToast({ message: 'Suggestion approved.', variant: 'success' })
    },
    onError: () => showToast({ message: 'Could not approve suggestion.', variant: 'error' }),
  })

  const rejectMutation = useMutation({
    mutationFn: postRejectSuggestion,
    onSuccess: () => {
      invalidate()
      showToast({ message: 'Suggestion dismissed.', variant: 'info' })
    },
    onError: () => showToast({ message: 'Could not dismiss suggestion.', variant: 'error' }),
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
        Failed to load review suggestions.
      </p>
    )
  }

  return (
    <div>
      <div className="mb-4 flex items-baseline justify-between">
        <h1 className="text-xl font-bold tracking-tight">Review</h1>
        <span className="text-xs text-slate-500 dark:text-slate-400">
          {data.length} suggestion{data.length === 1 ? '' : 's'} from full-scan runs
        </span>
      </div>

      {data.length === 0 ? (
        <p className="rounded-xl border border-dashed border-slate-200 p-6 text-center text-sm text-slate-500 dark:border-slate-700 dark:text-slate-400">
          Nothing to review right now.
        </p>
      ) : (
        <ul aria-live="polite" className="space-y-3">
          {data.map((suggestion) => {
            const before = parseSnapshot(suggestion.previous_extract_json)
            const after = parseSnapshot(suggestion.suggested_extract_json)
            const pending = approveMutation.isPending || rejectMutation.isPending
            return (
              <li
                key={suggestion.id}
                className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-700 dark:bg-slate-800"
              >
                <div className="mb-3 flex items-baseline justify-between">
                  <span className="text-sm font-semibold">{actionLabel(suggestion.action)}</span>
                  <span className="text-xs text-slate-500 dark:text-slate-400">
                    {new Date(suggestion.created_at).toLocaleString()}
                  </span>
                </div>
                <div className="mb-4 space-y-1.5">
                  <DiffRow label="Company" before={before?.company_name} after={after?.company_name} />
                  <DiffRow label="Job title" before={before?.job_title} after={after?.job_title} />
                  <DiffRow label="Status" before={before?.status} after={after?.status} />
                  {suggestion.action === 'reclassify_irrelevant' && (
                    <p className="text-sm text-slate-500 dark:text-slate-400">
                      Re-scanning this email no longer classifies it as a real application confirmation.
                      Approving just dismisses this suggestion - no data is deleted automatically.
                    </p>
                  )}
                </div>
                <div className="flex justify-end gap-2">
                  <button
                    type="button"
                    onClick={() => rejectMutation.mutate(suggestion.id)}
                    disabled={pending}
                    className="cursor-pointer rounded-lg border border-slate-200 px-3 py-1.5 text-sm font-medium transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-600 dark:hover:bg-slate-700"
                  >
                    Reject
                  </button>
                  <button
                    type="button"
                    onClick={() => approveMutation.mutate(suggestion.id)}
                    disabled={pending}
                    className="cursor-pointer rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    Approve
                  </button>
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
