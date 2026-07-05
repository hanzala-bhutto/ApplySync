import { useEffect, useRef } from 'react'

interface ConfirmDialogProps {
  open: boolean
  title: string
  description: string
  confirmLabel?: string
  onConfirm: () => void
  onCancel: () => void
}

// Native <dialog> gives real accessibility for free: focus trapping, ESC to
// close, and it's announced correctly by screen readers as a dialog - not
// something worth hand-rolling with divs and ARIA attributes we'd get subtly
// wrong.
export function ConfirmDialog({ open, title, description, confirmLabel = 'Confirm', onConfirm, onCancel }: ConfirmDialogProps) {
  const ref = useRef<HTMLDialogElement>(null)

  useEffect(() => {
    const dialog = ref.current
    if (!dialog) return
    if (open && !dialog.open) dialog.showModal()
    if (!open && dialog.open) dialog.close()
  }, [open])

  return (
    <dialog
      ref={ref}
      onCancel={onCancel}
      onClose={onCancel}
      // fixed + inset-0 + m-auto centers a dialog of unknown size in the
      // viewport. Native <dialog> normally centers itself via a default
      // `margin: auto`, but Tailwind's preflight reset zeroes margins on
      // every element, which silently breaks that and pins it to the top.
      className="fixed inset-0 m-auto w-full max-w-sm rounded-xl border border-slate-200 bg-white p-5 shadow-xl backdrop:bg-slate-900/40 dark:border-slate-800 dark:bg-slate-900"
    >
      <h2 className="mb-1.5 text-base font-semibold">{title}</h2>
      <p className="mb-4 text-sm text-slate-500 dark:text-slate-400">{description}</p>
      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm font-medium transition-colors hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={onConfirm}
          className="rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-brand-700"
        >
          {confirmLabel}
        </button>
      </div>
    </dialog>
  )
}
