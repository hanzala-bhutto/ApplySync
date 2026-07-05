import { useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  getApplicationDetail,
  getSourceEmail,
  patchFields,
  patchStatus,
  postReprocess,
  type Application,
  type StatusEvent,
} from '../lib/api'
import { avatarFor } from '../lib/avatar'
import { statusStyle, STATUS_STYLES } from '../lib/status'
import { useToast } from '../lib/toast'
import { ConfirmDialog } from '../components/ConfirmDialog'

export function ApplicationDetail() {
  const { id } = useParams<{ id: string }>()
  const applicationId = Number(id)
  const queryClient = useQueryClient()
  const { showToast } = useToast()
  const [editing, setEditing] = useState(false)
  const [confirmReprocess, setConfirmReprocess] = useState(false)

  const queryKey = ['application', applicationId]
  const { data, isLoading, isError } = useQuery({
    queryKey,
    queryFn: () => getApplicationDetail(applicationId),
    enabled: Number.isFinite(applicationId),
  })

  function invalidate() {
    queryClient.invalidateQueries({ queryKey })
    queryClient.invalidateQueries({ queryKey: ['dashboard'] })
  }

  const statusMutation = useMutation({
    mutationFn: (status: string) => patchStatus(applicationId, status),
    onSuccess: (updated) => {
      queryClient.setQueryData(queryKey, (old: typeof data) => (old ? { ...old, application: updated } : old))
      invalidate()
      showToast({ message: `Status set to ${updated.current_status}`, variant: 'success' })
    },
    onError: () => showToast({ message: 'Could not update status.', variant: 'error' }),
  })

  const fieldsMutation = useMutation({
    mutationFn: (fields: { company_name: string; job_title: string; platform: string }) =>
      patchFields(applicationId, fields),
    onSuccess: (updated) => {
      queryClient.setQueryData(queryKey, (old: typeof data) => (old ? { ...old, application: updated } : old))
      invalidate()
      setEditing(false)
      showToast({ message: 'Application updated.', variant: 'success' })
    },
    onError: () => showToast({ message: 'Could not save changes.', variant: 'error' }),
  })

  const reprocessMutation = useMutation({
    mutationFn: () => postReprocess(applicationId),
    onSuccess: (updated) => {
      queryClient.setQueryData(queryKey, (old: typeof data) => (old ? { ...old, application: updated } : old))
      invalidate()
      showToast({ message: 'Re-extracted from the original email.', variant: 'success' })
    },
    onError: () => showToast({ message: 'Reprocess failed.', variant: 'error' }),
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

      <section className="mb-8 rounded-xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-700 dark:bg-slate-800">
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

          <div className="flex flex-col items-end gap-1.5">
            <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium capitalize ${style.bg} ${style.text}`}>
              <span className={`h-1.5 w-1.5 rounded-full ${style.dot}`} aria-hidden="true" />
              {application.current_status}
            </span>
            <label htmlFor="status-select" className="sr-only">Change status</label>
            <select
              id="status-select"
              value={application.current_status}
              disabled={statusMutation.isPending}
              onChange={(e) => statusMutation.mutate(e.target.value)}
              className="rounded-lg border border-slate-200 bg-white px-2 py-1 text-xs capitalize focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-100 dark:border-slate-600 dark:bg-slate-700"
            >
              {Object.keys(STATUS_STYLES).map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </div>
        </div>

        <dl className="mb-4 grid grid-cols-2 gap-3 text-sm sm:grid-cols-3">
          <div>
            <dt className="text-slate-500 dark:text-slate-400">Platform</dt>
            <dd className="capitalize">{application.platform}</dd>
          </div>
          <div>
            <dt className="text-slate-500 dark:text-slate-400">Applied</dt>
            <dd>{application.applied_date}</dd>
          </div>
          {application.location && (
            <div><dt className="text-slate-500 dark:text-slate-400">Location</dt><dd>{application.location}</dd></div>
          )}
          {application.salary_text && (
            <div><dt className="text-slate-500 dark:text-slate-400">Salary</dt><dd>{application.salary_text}</dd></div>
          )}
          {application.job_url && (
            <div>
              <dt className="text-slate-500 dark:text-slate-400">Posting</dt>
              <dd><a className="text-brand-600 hover:underline dark:text-brand-400" href={application.job_url}>Link</a></dd>
            </div>
          )}
        </dl>

        <div className="flex gap-2 border-t border-slate-100 pt-4 dark:border-slate-700">
          <button
            type="button"
            onClick={() => setEditing((v) => !v)}
            aria-expanded={editing}
            className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm font-medium transition-colors hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-700"
          >
            {editing ? 'Cancel edit' : 'Edit'}
          </button>
          <button
            type="button"
            onClick={() => setConfirmReprocess(true)}
            disabled={reprocessMutation.isPending}
            className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm font-medium transition-colors hover:bg-slate-50 disabled:opacity-50 dark:border-slate-600 dark:hover:bg-slate-700"
          >
            {reprocessMutation.isPending ? 'Reprocessing...' : 'Reprocess from email'}
          </button>
        </div>

        {editing && <EditForm application={application} onSave={(fields) => fieldsMutation.mutate(fields)} saving={fieldsMutation.isPending} />}
      </section>

      <section>
        <h2 className="mb-3 text-sm font-semibold text-slate-500 dark:text-slate-400">Timeline</h2>
        <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm dark:border-slate-700 dark:bg-slate-800">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500 dark:bg-slate-700/50 dark:text-slate-400">
              <tr>
                <th className="px-4 py-2">Date</th>
                <th className="px-4 py-2">Status</th>
                <th className="px-4 py-2">Notes</th>
                <th className="px-4 py-2">Source</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-700">
              {timeline.map((event) => (
                <TimelineRow key={event.id} event={event} />
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <ConfirmDialog
        open={confirmReprocess}
        title="Reprocess from email?"
        description="This re-runs extraction on the original email and overwrites the company, title, and status fields below with a fresh result."
        confirmLabel="Reprocess"
        onCancel={() => setConfirmReprocess(false)}
        onConfirm={() => {
          setConfirmReprocess(false)
          reprocessMutation.mutate()
        }}
      />
    </div>
  )
}

function EditForm({
  application,
  onSave,
  saving,
}: {
  application: Application
  onSave: (fields: { company_name: string; job_title: string; platform: string }) => void
  saving: boolean
}) {
  const [companyName, setCompanyName] = useState(application.company_name)
  const [jobTitle, setJobTitle] = useState(application.job_title)
  const [platform, setPlatform] = useState(application.platform)

  return (
    <form
      className="mt-4 space-y-3 border-t border-slate-100 pt-4 dark:border-slate-700"
      onSubmit={(e) => {
        e.preventDefault()
        onSave({ company_name: companyName, job_title: jobTitle, platform })
      }}
    >
      <div>
        <label htmlFor="edit-company" className="mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400">Company</label>
        <input
          id="edit-company"
          value={companyName}
          onChange={(e) => setCompanyName(e.target.value)}
          required
          className="w-full rounded-lg border border-slate-200 px-3 py-1.5 text-sm focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-100 dark:border-slate-600 dark:bg-slate-700"
        />
      </div>
      <div>
        <label htmlFor="edit-title" className="mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400">Job title</label>
        <input
          id="edit-title"
          value={jobTitle}
          onChange={(e) => setJobTitle(e.target.value)}
          required
          className="w-full rounded-lg border border-slate-200 px-3 py-1.5 text-sm focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-100 dark:border-slate-600 dark:bg-slate-700"
        />
      </div>
      <div>
        <label htmlFor="edit-platform" className="mb-1 block text-xs font-medium text-slate-500 dark:text-slate-400">Platform</label>
        <input
          id="edit-platform"
          value={platform}
          onChange={(e) => setPlatform(e.target.value)}
          required
          className="w-full rounded-lg border border-slate-200 px-3 py-1.5 text-sm focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-100 dark:border-slate-600 dark:bg-slate-700"
        />
      </div>
      <button
        type="submit"
        disabled={saving}
        className="rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-brand-700 disabled:opacity-50"
      >
        {saving ? 'Saving...' : 'Save'}
      </button>
    </form>
  )
}

const EMAIL_PREVIEW_CHARS = 500

function TimelineRow({ event }: { event: StatusEvent }) {
  const [open, setOpen] = useState(false)

  return (
    <>
      <tr>
        <td className="px-4 py-2 text-slate-500 dark:text-slate-400">{event.event_date}</td>
        <td className="px-4 py-2 capitalize">{event.status}</td>
        <td className="px-4 py-2 text-slate-500 dark:text-slate-400">{event.notes ?? ''}</td>
        <td className="px-4 py-2">
          {event.source_email_id ? (
            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              aria-expanded={open}
              className="text-xs font-medium text-brand-600 hover:underline dark:text-brand-400"
            >
              {open ? 'Hide email' : 'View email'}
            </button>
          ) : (
            <span className="text-xs text-slate-400 dark:text-slate-500">manual</span>
          )}
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={4} className="border-t border-slate-100 bg-slate-50 px-4 py-3 dark:border-slate-700 dark:bg-slate-700/30">
            <SourceEmailPanel eventId={event.id} />
          </td>
        </tr>
      )}
    </>
  )
}

function SourceEmailPanel({ eventId }: { eventId: number }) {
  const [showFull, setShowFull] = useState(false)
  const { data, isLoading, isError } = useQuery({
    queryKey: ['source-email', eventId],
    queryFn: () => getSourceEmail(eventId),
    staleTime: Infinity,
  })

  if (isLoading) {
    return <p className="text-xs text-slate-500 dark:text-slate-400" role="status">Loading original email...</p>
  }
  if (isError || !data) {
    return <p className="text-xs text-rose-500" role="alert">Could not load the original email.</p>
  }

  const truncated = data.body.length > EMAIL_PREVIEW_CHARS && !showFull
  const bodyToShow = truncated ? `${data.body.slice(0, EMAIL_PREVIEW_CHARS)}…` : data.body

  return (
    <div className="text-sm">
      <dl className="mb-2 grid grid-cols-1 gap-x-4 gap-y-1 sm:grid-cols-3">
        <div><dt className="text-xs text-slate-500 dark:text-slate-400">From</dt><dd className="truncate">{data.sender}</dd></div>
        <div><dt className="text-xs text-slate-500 dark:text-slate-400">Date</dt><dd className="truncate">{data.date}</dd></div>
        <div><dt className="text-xs text-slate-500 dark:text-slate-400">Subject</dt><dd className="truncate">{data.subject}</dd></div>
      </dl>
      <pre className="max-h-80 overflow-y-auto whitespace-pre-wrap break-words rounded-lg border border-slate-200 bg-white p-3 text-xs text-slate-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300">
        {bodyToShow}
      </pre>
      {data.body.length > EMAIL_PREVIEW_CHARS && (
        <button
          type="button"
          onClick={() => setShowFull((v) => !v)}
          className="mt-1.5 text-xs font-medium text-brand-600 hover:underline dark:text-brand-400"
        >
          {showFull ? 'Show less' : 'Show full email'}
        </button>
      )}
    </div>
  )
}
