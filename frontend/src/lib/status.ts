// Ported from web/app.py's STATUS_STYLES so both frontends agree.
export interface StatusStyle {
  bg: string
  text: string
  dot: string
}

export const STATUS_STYLES: Record<string, StatusStyle> = {
  applied: { bg: 'bg-slate-100 dark:bg-slate-700', text: 'text-slate-700 dark:text-slate-300', dot: 'bg-slate-400' },
  viewed: { bg: 'bg-sky-50 dark:bg-sky-900', text: 'text-sky-700 dark:text-sky-300', dot: 'bg-sky-500' },
  assessment: { bg: 'bg-cyan-50 dark:bg-cyan-900', text: 'text-cyan-700 dark:text-cyan-300', dot: 'bg-cyan-500' },
  interview: { bg: 'bg-violet-50 dark:bg-violet-900', text: 'text-violet-700 dark:text-violet-300', dot: 'bg-violet-500' },
  offer: { bg: 'bg-emerald-50 dark:bg-emerald-900', text: 'text-emerald-700 dark:text-emerald-300', dot: 'bg-emerald-500' },
  declined: { bg: 'bg-orange-50 dark:bg-orange-900', text: 'text-orange-700 dark:text-orange-300', dot: 'bg-orange-500' },
  rejected: { bg: 'bg-rose-50 dark:bg-rose-900', text: 'text-rose-700 dark:text-rose-300', dot: 'bg-rose-500' },
  other: { bg: 'bg-amber-50 dark:bg-amber-900', text: 'text-amber-700 dark:text-amber-300', dot: 'bg-amber-500' },
}

export function statusStyle(status: string): StatusStyle {
  return STATUS_STYLES[status] ?? STATUS_STYLES.other
}
