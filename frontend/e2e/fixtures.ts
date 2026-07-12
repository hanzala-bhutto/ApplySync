import type { Page } from '@playwright/test'

export const STATUS_ORDER = ['applied', 'viewed', 'assessment', 'interview', 'offer', 'declined', 'rejected', 'other']

export function makeApplication(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: 1,
    company_name: 'Acme Corp',
    job_title: 'Senior Backend Engineer',
    platform: 'linkedin',
    job_url: 'https://example.com/job/1',
    location: 'Berlin, Germany',
    salary_text: '70000-85000 EUR',
    applied_date: '2026-01-15',
    current_status: 'applied',
    created_at: '2026-01-15T10:00:00Z',
    updated_at: '2026-01-15T10:00:00Z',
    ...overrides,
  }
}

export function makeBoard() {
  const apps = [
    makeApplication({ id: 1, company_name: 'Acme Corp', current_status: 'applied' }),
    makeApplication({ id: 2, company_name: 'Globex', current_status: 'interview', job_title: 'Platform Engineer' }),
    makeApplication({ id: 3, company_name: 'Initech', current_status: 'rejected', job_title: 'Staff Engineer' }),
  ]
  const board: Record<string, unknown[]> = Object.fromEntries(STATUS_ORDER.map((s) => [s, []]))
  for (const app of apps) {
    ;(board[app.current_status as string] as unknown[]).push(app)
  }
  return { board, apps }
}

export function dashboardResponse() {
  const { board, apps } = makeBoard()
  return {
    board,
    status_order: STATUS_ORDER,
    breakdown: [
      { platform: 'linkedin', total: 2, responded: 1 },
      { platform: 'other', total: 1, responded: 1 },
    ],
    reminders: [apps[0]],
    reminders_total: 1,
    filter_options: {
      years: [2025, 2026],
      platforms: ['linkedin', 'other'],
      statuses: STATUS_ORDER,
    },
  }
}

/** Registers mocked handlers for every /api/* route the frontend calls, so
 * tests never depend on a real FastAPI backend or real Gmail-derived data. */
export async function mockApi(page: Page) {
  const dashboard = dashboardResponse()

  await page.route('**/api/dashboard*', async (route) => {
    await route.fulfill({ json: dashboard })
  })

  // Connected by default so the Gmail connection banner doesn't show up and
  // interfere with unrelated tests/layout/a11y checks. Tests that care about
  // the disconnected state override this route themselves.
  await page.route('**/api/gmail/status*', async (route) => {
    await route.fulfill({ json: { connected: true } })
  })

  // Idle by default (never synced) - tests that care about an in-progress or
  // just-finished sync override this route themselves.
  await page.route('**/api/sync/status*', async (route) => {
    await route.fulfill({
      json: { in_progress: false, last_error: null, current_run_type: null, latest_run: null, history: [] },
    })
  })

  // No pending suggestions by default - tests for the Review page override this themselves.
  await page.route('**/api/review-suggestions', async (route) => {
    await route.fulfill({ json: [] })
  })

  await page.route('**/api/reminders*', async (route) => {
    await route.fulfill({
      json: { items: dashboard.reminders, total: dashboard.reminders_total, page: 1, page_size: 20 },
    })
  })

  await page.route('**/api/applications/*/status', async (route) => {
    const body = route.request().postDataJSON() as { status: string }
    await route.fulfill({ json: makeApplication({ current_status: body.status }) })
  })

  await page.route('**/api/applications/*/reprocess', async (route) => {
    await route.fulfill({ json: makeApplication({ job_title: 'Re-extracted Title' }) })
  })

  await page.route('**/api/applications/*/research*', async (route) => {
    await route.fulfill({
      json: {
        company_name: 'Acme Corp',
        summary: 'Acme Corp builds industrial widgets and automation hardware.',
        industry: 'Industrial manufacturing',
        company_size: '1001-5000',
        headquarters: 'Cupertino, USA',
        website: 'https://acme.example',
        recent_news: 'Announced a new automation division last quarter.',
        source_urls: ['https://acme.example', 'https://news.example/acme'],
        researched_at: '2026-07-12T09:00:00Z',
      },
    })
  })

  await page.route('**/api/status-events/*/email', async (route) => {
    await route.fulfill({
      json: {
        subject: 'Your application to Acme Corp',
        sender: 'noreply@acme.example',
        date: 'Thu, 15 Jan 2026 10:00:00 +0000',
        body: 'Thanks for applying to the Senior Backend Engineer role at Acme Corp. We will review your application and get back to you soon.',
      },
    })
  })

  await page.route('**/api/applications/*', async (route) => {
    const method = route.request().method()
    if (method === 'GET') {
      await route.fulfill({
        json: {
          application: makeApplication(),
          timeline: [
            { id: 1, application_id: 1, status: 'applied', event_date: '2026-01-15', source_email_id: 'msg1', raw_extract_json: null, notes: null, created_at: '2026-01-15T10:00:00Z' },
          ],
        },
      })
      return
    }
    if (method === 'PATCH') {
      const body = route.request().postDataJSON() as Record<string, string>
      await route.fulfill({ json: makeApplication(body) })
      return
    }
    await route.continue()
  })
}
