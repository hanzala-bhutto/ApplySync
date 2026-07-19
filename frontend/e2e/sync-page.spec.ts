import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { mockApi } from './fixtures'

function pipelineRun(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: 'run-1',
    started_at: '2026-01-15T10:00:00Z',
    finished_at: '2026-01-15T10:01:00Z',
    emails_fetched: 5,
    emails_relevant: 3,
    applications_created: 2,
    events_created: 2,
    errors: null,
    emails_total: 5,
    emails_scrutinized: 5,
    emails_extracted: 4,
    emails_written: 3,
    updated_at: '2026-01-15T10:01:00Z',
    run_type: 'incremental',
    suggestions_created: 0,
    ...overrides,
  }
}

test.beforeEach(async ({ page }) => {
  await mockApi(page)
})

test('sync page shows a message when no sync has run yet', async ({ page }) => {
  await page.goto('/sync')
  await expect(page.getByRole('heading', { name: 'Sync' })).toBeVisible()
  await expect(page.getByText('No sync has run yet.')).toBeVisible()
})

test('sync page shows stage progress bars and counts for the latest run', async ({ page }) => {
  await page.route('**/api/sync/status*', async (route) => {
    await route.fulfill({
      json: {
        in_progress: false,
        last_error: null,
        current_run_type: null,
        latest_run: pipelineRun(),
        history: [pipelineRun()],
      },
    })
  })

  await page.goto('/sync')
  await expect(page.getByText('5 emails found')).toBeVisible()
  await expect(page.getByText('5 / 5')).toBeVisible() // scrutiny
  await expect(page.getByText('4 / 5')).toBeVisible() // extraction
  await expect(page.getByText(/2 new, 2 updates from 3 relevant emails/)).toBeVisible()
})

test('sync page lists recent run history', async ({ page }) => {
  await page.route('**/api/sync/status*', async (route) => {
    await route.fulfill({
      json: {
        in_progress: false,
        last_error: null,
        current_run_type: null,
        latest_run: pipelineRun(),
        history: [
          pipelineRun({ id: 'run-1' }),
          pipelineRun({ id: 'run-0', errors: 'boom' }),
          // Deliberately the pre-rename value (see docs/feasibility/full-audit-
          // rename.md) - checks that an older stored run still displays under
          // the current label, not just newly-created 'full_audit' runs.
          pipelineRun({ id: 'run-2', run_type: 'full_scan' }),
        ],
      },
    })
  })

  await page.goto('/sync')
  await expect(page.getByRole('heading', { name: 'Recent runs' })).toBeVisible()
  await expect(page.getByRole('cell', { name: 'Completed' }).first()).toBeVisible()
  await expect(page.getByRole('cell', { name: 'Failed' })).toBeVisible()
  await expect(page.getByRole('cell', { name: 'Full Audit' })).toBeVisible()
})

test('sync page has no detectable accessibility violations', async ({ page }) => {
  await page.route('**/api/sync/status*', async (route) => {
    await route.fulfill({
      json: {
        in_progress: false,
        last_error: null,
        current_run_type: null,
        latest_run: pipelineRun(),
        history: [pipelineRun()],
      },
    })
  })

  await page.goto('/sync')
  await expect(page.getByRole('heading', { name: 'Sync' })).toBeVisible()
  const results = await new AxeBuilder({ page }).analyze()
  expect(results.violations).toEqual([])
})

test('sync page shows a Full Audit control gated behind a confirm dialog', async ({ page }) => {
  let posted = false
  await page.route('**/api/sync/full-audit', async (route) => {
    if (route.request().method() === 'POST') {
      posted = true
      await route.fulfill({ status: 202, json: { status: 'started' } })
      return
    }
    await route.continue()
  })

  await page.goto('/sync')
  await expect(page.getByRole('heading', { name: 'Full Audit' })).toBeVisible()

  await page.getByRole('button', { name: 'Run Full Audit' }).first().click()
  await expect(page.getByRole('heading', { name: 'Run a full audit?' })).toBeVisible()

  // Cancelling must not trigger the request.
  await page.getByRole('button', { name: 'Cancel' }).click()
  await expect(page.getByRole('heading', { name: 'Run a full audit?' })).not.toBeVisible()
  expect(posted).toBe(false)

  await page.getByRole('button', { name: 'Run Full Audit' }).first().click()
  await page.getByRole('button', { name: 'Run Full Audit' }).last().click()
  await expect.poll(() => posted).toBe(true)
})

test('sync page labels progress by the in-progress run type', async ({ page }) => {
  await page.route('**/api/sync/status*', async (route) => {
    await route.fulfill({
      json: {
        in_progress: true,
        last_error: null,
        current_run_type: 'full_audit',
        latest_run: pipelineRun({ finished_at: null, emails_scrutinized: 2, emails_extracted: 1, emails_written: 0 }),
        history: [],
      },
    })
  })

  await page.goto('/sync')
  await expect(page.getByText('Full Audit in progress...')).toBeVisible()
})

test('sync page shows suggestion count for a finished full audit, not the always-zero application/event counts', async ({ page }) => {
  await page.route('**/api/sync/status*', async (route) => {
    await route.fulfill({
      json: {
        in_progress: false,
        last_error: null,
        current_run_type: null,
        latest_run: pipelineRun({ run_type: 'full_audit', suggestions_created: 3, emails_relevant: 10 }),
        history: [],
      },
    })
  })

  await page.goto('/sync')
  await expect(page.getByText(/3 suggestions queued for/)).toBeVisible()
  await expect(page.locator('#main-content').getByRole('link', { name: 'review' })).toHaveAttribute(
    'href',
    '/review'
  )
})

test('sync page shows a clean-audit message when a full audit finds nothing to review', async ({ page }) => {
  await page.route('**/api/sync/status*', async (route) => {
    await route.fulfill({
      json: {
        in_progress: false,
        last_error: null,
        current_run_type: null,
        latest_run: pipelineRun({ run_type: 'full_audit', suggestions_created: 0, emails_relevant: 10 }),
        history: [],
      },
    })
  })

  await page.goto('/sync')
  await expect(page.getByText('nothing needed review.')).toBeVisible()
})

test('sync page shows a Stop control while in progress and reports a stopped run distinctly from a failed one', async ({ page }) => {
  let stopRequested = false
  let stopping = false
  await page.route('**/api/sync/stop', async (route) => {
    stopRequested = true
    stopping = true
    await route.fulfill({ status: 200, json: { status: 'stopping' } })
  })

  let inProgress = true
  await page.route('**/api/sync/status*', async (route) => {
    await route.fulfill({
      json: {
        in_progress: inProgress,
        last_error: null,
        current_run_type: inProgress ? 'incremental' : null,
        stopping: inProgress ? stopping : false,
        latest_run: pipelineRun({
          finished_at: inProgress ? null : '2026-01-15T10:01:00Z',
          emails_written: 2,
          emails_total: 5,
          errors: inProgress ? null : 'cancelled_by_user',
        }),
        history: [],
      },
    })
  })

  await page.goto('/sync')
  // Scoped to #main-content: the header's own SyncButton also renders a
  // Stop control while in_progress, so an unscoped locator is ambiguous.
  const main = page.locator('#main-content')
  const stopButton = main.getByRole('button', { name: 'Stop' })
  await expect(stopButton).toBeVisible()

  await stopButton.click()
  await expect.poll(() => stopRequested).toBe(true)
  await expect(main.getByRole('button', { name: 'Stopping…' })).toBeVisible()
  // Both the status paragraph and the mutation's toast use this exact
  // wording (a deliberate echo, not a bug) - scope to main content since an
  // unscoped locator matches the toast too.
  await expect(main.getByText('Stopping after the current email finishes...')).toBeVisible()

  inProgress = false
  await expect(main.getByText('Stopped: 2 of 5 emails processed before you stopped it.')).toBeVisible()
  // The Stop button itself must disappear once the run has actually finished.
  await expect(main.getByRole('button', { name: /Stop/ })).not.toBeVisible()
})

test('sync page recent-runs table labels a cancelled run "Stopped", not "Failed"', async ({ page }) => {
  await page.route('**/api/sync/status*', async (route) => {
    await route.fulfill({
      json: {
        in_progress: false,
        last_error: null,
        current_run_type: null,
        stopping: false,
        latest_run: pipelineRun({ errors: 'cancelled_by_user' }),
        history: [pipelineRun({ id: 'run-1', errors: 'cancelled_by_user' }), pipelineRun({ id: 'run-2', errors: 'boom' })],
      },
    })
  })

  await page.goto('/sync')
  await expect(page.getByRole('cell', { name: 'Stopped' })).toBeVisible()
  await expect(page.getByRole('cell', { name: 'Failed' })).toBeVisible()
})
