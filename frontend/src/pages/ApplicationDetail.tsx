import { useParams, Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { getApplicationDetail } from '../lib/api'
import { avatarFor } from '../lib/avatar'
import { statusStyle } from '../lib/status'

export function ApplicationDetail() {
  const { id } = useParams<{ id: string }>()
  const applicationId = Number(id)

  const { data, isLoading, isError } = useQuery({
    queryKey: ['application', applicationId],
    queryFn: () => getApplicationDetail(applicationId),
    enabled: Number.isFinite(applicationId),
  })

  if (isLoading) {
    return <p className="text-sm text-slate-400" role="status">Loading...</p>
  }
  if (isError || !data) {
    return (
      <div>
        <p className="text-sm text-slate-500 dark:text-slate-400">No application with id {id}.</p>
        <p className="mt-3">
          <Link to="/" className="text-sm font-medium text-brand-600 hover:underline dark:text-brand-400">
            &larr; back to dashboard
          </Link>
        </p>
      </div>
    )
  }

  const { application, timeline } = data
  const style = statusStyle(application.current_status)
  const av = avatarFor(application.company_name)

  return (
    <div>
      <p className="mb-4">
        <Link to="/" className="text-sm font-medium text-brand-600 hover:underline dark:text-brand-400">
          &larr; back to dashboard
        </Link>
      </p>

      <section className="mb-8 rounded-xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <div className="mb-4 flex items-start justify-between gap-3">
          <div className="flex items-start gap-3">
            <span className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-full ${av.bg} text-base font-bold text-white`}>
              {av.initial}
            </span>
            <div>
              <h1 className="text-lg font-bold tracking-tight">{application.company_name}</h1>
              <p className="text-sm text-slate-500 dark:text-slate-400">{application.job_title}</p>
            </div>
          </div>
          <span className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium capitalize ${style.bg} ${style.text}`}>
            <span className={`h-1.5 w-1.5 rounded-full ${style.dot}`} aria-hidden="true" />
            {application.current_status}
          </span>
        </div>

        <dl className="mb-4 grid grid-cols-2 gap-3 text-sm sm:grid-cols-3">
          <div>
            <dt className="text-slate-400 dark:text-slate-500">Platform</dt>
            <dd className="capitalize">{application.platform}</dd>
          </div>
          <div>
            <dt className="text-slate-400 dark:text-slate-500">Applied</dt>
            <dd>{application.applied_date}</dd>
          </div>
          {application.location && (
            <div><dt className="text-slate-400 dark:text-slate-500">Location</dt><dd>{application.location}</dd></div>
          )}
          {application.salary_text && (
            <div><dt className="text-slate-400 dark:text-slate-500">Salary</dt><dd>{application.salary_text}</dd></div>
          )}
          {application.job_url && (
            <div>
              <dt className="text-slate-400 dark:text-slate-500">Posting</dt>
              <dd><a className="text-brand-600 hover:underline dark:text-brand-400" href={application.job_url}>Link</a></dd>
            </div>
          )}
        </dl>
      </section>

      <section>
        <h2 className="mb-3 text-sm font-semibold text-slate-500 dark:text-slate-400">Timeline</h2>
        <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-400 dark:bg-slate-800/50 dark:text-slate-500">
              <tr>
                <th className="px-4 py-2">Date</th>
                <th className="px-4 py-2">Status</th>
                <th className="px-4 py-2">Notes</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {timeline.map((event) => (
                <tr key={event.id}>
                  <td className="px-4 py-2 text-slate-500 dark:text-slate-400">{event.event_date}</td>
                  <td className="px-4 py-2 capitalize">{event.status}</td>
                  <td className="px-4 py-2 text-slate-500 dark:text-slate-400">{event.notes ?? ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}
