import type { ReactNode } from 'react'
import { Link, NavLink } from 'react-router-dom'

const NAV_LINKS = [
  { to: '/', label: 'Dashboard', end: true },
  { to: '/reminders', label: 'Follow-Up' },
  { to: '/analytics', label: 'Analytics' },
]

export function Layout({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-full font-sans antialiased bg-slate-50 text-slate-900 dark:bg-slate-900 dark:text-slate-100">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-white focus:px-3 focus:py-2 focus:text-sm focus:font-medium focus:shadow-lg dark:focus:bg-slate-700"
      >
        Skip to content
      </a>
      <header className="sticky top-0 z-10 border-b border-slate-200/80 bg-white/80 backdrop-blur dark:border-slate-700/80 dark:bg-slate-900/80">
        <div className="mx-auto flex max-w-7xl items-center gap-6 px-6 py-4">
          <Link to="/" className="flex items-center gap-2.5">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-brand-500 to-brand-700 text-sm font-bold text-white shadow-sm">
              A
            </span>
            <span className="text-[17px] font-bold tracking-tight">ApplySync</span>
          </Link>
          <nav className="flex items-center gap-1">
            {NAV_LINKS.map((link) => (
              <NavLink
                key={link.to}
                to={link.to}
                end={link.end}
                className={({ isActive }) =>
                  `rounded-lg px-2.5 py-1.5 text-sm font-medium transition-colors ${
                    isActive
                      ? 'bg-brand-50 text-brand-700 dark:bg-brand-900/40 dark:text-brand-300'
                      : 'text-slate-500 hover:bg-slate-100 hover:text-slate-900 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-100'
                  }`
                }
              >
                {link.label}
              </NavLink>
            ))}
          </nav>
          <span className="ml-auto hidden text-sm text-slate-500 sm:inline dark:text-slate-400">
            your job applications, in one place
          </span>
        </div>
      </header>
      <main id="main-content" className="mx-auto max-w-7xl px-6 py-8">
        {children}
      </main>
    </div>
  )
}
