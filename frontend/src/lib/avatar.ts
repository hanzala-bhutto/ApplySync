// Deterministic per-company avatar color (same company always gets the same
// color), ported from web/app.py's _avatar so both frontends agree.
const AVATAR_PALETTE = [
  'bg-rose-500', 'bg-orange-500', 'bg-amber-500', 'bg-lime-500',
  'bg-emerald-500', 'bg-teal-500', 'bg-cyan-500', 'bg-blue-500',
  'bg-indigo-500', 'bg-violet-500', 'bg-fuchsia-500', 'bg-pink-500',
]

export function avatarFor(companyName: string): { bg: string; initial: string } {
  const sum = [...companyName].reduce((acc, c) => acc + c.charCodeAt(0), 0)
  const bg = AVATAR_PALETTE[sum % AVATAR_PALETTE.length]
  const initial = [...companyName].find((c) => /[a-zA-Z0-9]/.test(c)) ?? '?'
  return { bg, initial: initial.toUpperCase() }
}
