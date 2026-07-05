import { useEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getGmailStatus, gmailConnectUrl } from '../lib/api'
import { useToast } from '../lib/toast'

// Handles the redirect back from the backend's OAuth callback
// (?gmail=connected or ?gmail=error appended to whatever page the user
// started from) - shows a toast once, then strips the param so a refresh
// doesn't re-show it.
function useGmailRedirectResult(onConnected: () => void) {
  const { showToast } = useToast()
  const handled = useRef(false)

  useEffect(() => {
    if (handled.current) return
    const params = new URLSearchParams(window.location.search)
    const result = params.get('gmail')
    if (!result) return
    handled.current = true

    if (result === 'connected') {
      showToast({ message: 'Gmail connected.', variant: 'success' })
      onConnected()
    } else if (result === 'error') {
      showToast({ message: 'Gmail connection failed. Please try again.', variant: 'error' })
    }

    params.delete('gmail')
    const query = params.toString()
    const nextUrl = `${window.location.pathname}${query ? `?${query}` : ''}`
    window.history.replaceState(null, '', nextUrl)
    // Runs once per mount by design (see handled ref) - onConnected/showToast
    // are stable enough in practice that re-running this on every render
    // would just be noise.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
}

export function GmailConnectionBanner() {
  const { data, isLoading, refetch } = useQuery({
    queryKey: ['gmail-status'],
    queryFn: getGmailStatus,
  })

  useGmailRedirectResult(refetch)

  if (isLoading || data?.connected) return null

  return (
    <div className="border-b border-amber-200 bg-amber-50 px-6 py-2.5 text-sm dark:border-amber-800 dark:bg-amber-900/40">
      <div className="mx-auto flex max-w-7xl items-center justify-between gap-3">
        <span className="text-amber-800 dark:text-amber-200">
          Gmail isn't connected yet - syncing applications needs access to your inbox.
        </span>
        <a
          href={gmailConnectUrl(window.location.origin + window.location.pathname + window.location.search)}
          className="shrink-0 rounded-lg bg-amber-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-amber-700"
        >
          Connect Gmail
        </a>
      </div>
    </div>
  )
}
