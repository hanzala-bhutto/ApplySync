import { useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery, keepPreviousData } from '@tanstack/react-query'
import { getReminders } from '../lib/api'

const PAGE_SIZE = 20

export function Reminders() {
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()
  const page = Math.max(1, Number(searchParams.get('page') ?? '1') || 1)

  const { data, isLoading, isError } = useQuery({
    queryKey: ['reminders', page],
    queryFn: () => getReminders(page, PAGE_SIZE),
    placeholderData: keepPreviousData,
  })

  function goToPage(next: number) {
    const params = new URLSearchParams(searchParams)
    params.set('page', String(next))
    setSearchParams(params)
  }

  if (isLoading) {
    return <p className="text-sm text-slate-400" role="status">Loading...</p>
  }
  if (isError || !data) {
    return <p className="text-sm text-rose-500" role="alert">Failed to load reminders.</p>
  }

  const totalPages = Math.max(1, Math.ceil(data.total / data.page_size))

  return (
    <div>
      <div className="mb-4 flex items-baseline justify-between">
        <h1 className="text-xl font-bold tracking-tight">Needs Follow-Up</h1>
        <span className="text-xs text-slate-500 dark:text-slate-400">
          {data.total} application{data.total === 1 ? '' : 's'} applied 14+ days ago with no update, oldest first
        </span>
      </div>

      {data.items.length === 0 ? (
        <p className="rounded-xl border border-dashed border-slate-200 p-6 text-center text-sm text-slate-500 dark:border-slate-700 dark:text-slate-400">
          Nothing needs follow-up right now.
        </p>
      ) : (
        <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm dark:border-slate-700 dark:bg-slate-800">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500 dark:bg-slate-700/50 dark:text-slate-400">
              <tr>
                <th className="px-4 py-2">Company</th>
                <th className="px-4 py-2">Job Title</th>
                <th className="px-4 py-2">Platform</th>
                <th className="px-4 py-2">Applied</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-700">
              {data.items.map((application) => (
                <tr
                  key={application.id}
                  tabIndex={0}
                  role="button"
                  onClick={() => navigate(`/applications/${application.id}`)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') navigate(`/applications/${application.id}`)
                  }}
                  className="cursor-pointer transition-colors hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 dark:hover:bg-slate-700/50"
                >
                  <td className="px-4 py-2 font-medium">{application.company_name}</td>
                  <td className="px-4 py-2 text-slate-500 dark:text-slate-400">{application.job_title}</td>
                  <td className="px-4 py-2 capitalize text-slate-500 dark:text-slate-400">{application.platform}</td>
                  <td className="px-4 py-2 text-slate-500 dark:text-slate-400">{application.applied_date}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {totalPages > 1 && (
        <div className="mt-4 flex items-center justify-between text-sm">
          <button
            type="button"
            disabled={page <= 1}
            onClick={() => goToPage(page - 1)}
            className="rounded-lg border border-slate-200 px-3 py-1.5 font-medium transition-colors hover:bg-slate-50 disabled:opacity-40 dark:border-slate-700 dark:hover:bg-slate-700"
          >
            Previous
          </button>
          <span className="text-slate-500 dark:text-slate-400">
            Page {data.page} of {totalPages}
          </span>
          <button
            type="button"
            disabled={page >= totalPages}
            onClick={() => goToPage(page + 1)}
            className="rounded-lg border border-slate-200 px-3 py-1.5 font-medium transition-colors hover:bg-slate-50 disabled:opacity-40 dark:border-slate-700 dark:hover:bg-slate-700"
          >
            Next
          </button>
        </div>
      )}
    </div>
  )
}
