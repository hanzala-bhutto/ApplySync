import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'

export function Layout({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-full font-sans antialiased bg-slate-50 text-slate-900 dark:bg-slate-950 dark:text-slate-100">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-white focus:px-3 focus:py-2 focus:text-sm focus:font-medium focus:shadow-lg dark:focus:bg-slate-800"
      >
        Skip to content
      </a>
      <header className="sticky top-0 z-10 border-b border-slate-200/80 bg-white/80 backdrop-blur dark:border-slate-800/80 dark:bg-slate-950/80">
        <div className="mx-auto flex max-w-7xl items-center gap-3 px-6 py-4">
          <Link to="/" className="flex items-center gap-2.5">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-brand-500 to-brand-700 text-sm font-bold text-white shadow-sm">
              A
            </span>
            <span className="text-[17px] font-bold tracking-tight">ApplySync</span>
          </Link>
          <span className="hidden text-sm text-slate-400 sm:inline dark:text-slate-500">
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
