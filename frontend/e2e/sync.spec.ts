import { test, expect } from '@playwright/test'
import { mockApi } from './fixtures'

test('sync button shows "Never synced" and starts a sync on click', async ({ page }) => {
  await mockApi(page)

  let posted = false
  await page.route('**/api/sync', async (route) => {
    if (route.request().method() === 'POST') {
      posted = true
      await route.fulfill({ status: 202, json: { status: 'started' } })
      return
    }
    await route.continue()
  })

  await page.goto('/')
  await expect(page.getByText('Never synced')).toBeVisible()

  await page.getByRole('button', { name: 'Sync Now' }).click()
  await expect.poll(() => posted).toBe(true)
})

test('sync button polls while in progress and shows a toast on completion', async ({ page }) => {
  await mockApi(page)

  let inProgress = true
  await page.route('**/api/sync/status*', async (route) => {
    await route.fulfill({
      json: inProgress
        ? { in_progress: true, last_error: null, latest_run: null }
        : {
            in_progress: false,
            last_error: null,
            latest_run: {
              id: 'run-1',
              started_at: '2026-01-15T10:00:00Z',
              finished_at: '2026-01-15T10:01:00Z',
              emails_fetched: 5,
              emails_relevant: 3,
              applications_created: 2,
              events_created: 2,
              errors: null,
            },
          },
    })
  })

  await page.goto('/')
  await expect(page.getByRole('button', { name: 'Syncing...' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Syncing...' })).toBeDisabled()

  inProgress = false
  await expect(page.getByText(/Synced: 2 new, 2 updates from 3 relevant emails\./)).toBeVisible()
})

test('sync button shows an error toast when a sync fails', async ({ page }) => {
  await mockApi(page)

  let inProgress = true
  await page.route('**/api/sync/status*', async (route) => {
    await route.fulfill({
      json: inProgress
        ? { in_progress: true, last_error: null, latest_run: null }
        : { in_progress: false, last_error: 'Gmail API unreachable', latest_run: null },
    })
  })

  await page.goto('/')
  await expect(page.getByRole('button', { name: 'Syncing...' })).toBeVisible()

  inProgress = false
  // Plain-language message, not the raw backend exception text
  // ("Gmail API unreachable") - matches every other mutation's error toast.
  await expect(page.getByText('Sync failed. Check the server terminal for details.')).toBeVisible()
})

test('sync button shows a Stop control while in progress, calls the stop endpoint, and toasts on cancellation', async ({ page }) => {
  await mockApi(page)

  let stopped = false
  let stopping = false
  await page.route('**/api/sync/stop', async (route) => {
    stopped = true
    stopping = true
    await route.fulfill({ status: 200, json: { status: 'stopping' } })
  })

  let inProgress = true
  await page.route('**/api/sync/status*', async (route) => {
    await route.fulfill({
      json: inProgress
        ? { in_progress: true, last_error: null, stopping, latest_run: null }
        : {
            in_progress: false,
            last_error: null,
            stopping: false,
            latest_run: {
              id: 'run-1',
              started_at: '2026-01-15T10:00:00Z',
              finished_at: '2026-01-15T10:01:00Z',
              emails_fetched: 2,
              emails_relevant: 1,
              applications_created: 0,
              events_created: 0,
              errors: 'cancelled_by_user',
            },
          },
    })
  })

  await page.goto('/')
  const stopButton = page.getByRole('button', { name: 'Stop' })
  await expect(stopButton).toBeVisible()

  await stopButton.click()
  await expect.poll(() => stopped).toBe(true)
  await expect(page.getByRole('button', { name: 'Stopping…' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Stopping…' })).toBeDisabled()

  inProgress = false
  await expect(page.getByText('Sync stopped.')).toBeVisible()
})
