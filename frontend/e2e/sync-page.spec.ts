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
      json: { in_progress: false, last_error: null, latest_run: pipelineRun(), history: [pipelineRun()] },
    })
  })

  await page.goto('/sync')
  await expect(page.getByText('5 emails found')).toBeVisible()
  await expect(page.getByText('5 / 5')).toBeVisible() // scrutiny
  await expect(page.getByText('4 / 5')).toBeVisible() // extraction
  await expect(page.getByText(/2 new, 2 updates from 3 relevant emails/)).toBeVisible()
})

test('sync page triggers a sync from its own button, same as the header widget', async ({ page }) => {
  let posted = false
  await page.route('**/api/sync', async (route) => {
    if (route.request().method() === 'POST') {
      posted = true
      await route.fulfill({ status: 202, json: { status: 'started' } })
      return
    }
    await route.continue()
  })

  await page.goto('/sync')
  await page.locator('#main-content').getByRole('button', { name: 'Sync Now' }).click()
  await expect.poll(() => posted).toBe(true)
})

test('sync page lists recent run history', async ({ page }) => {
  await page.route('**/api/sync/status*', async (route) => {
    await route.fulfill({
      json: {
        in_progress: false,
        last_error: null,
        latest_run: pipelineRun(),
        history: [pipelineRun({ id: 'run-1' }), pipelineRun({ id: 'run-0', errors: 'boom' })],
      },
    })
  })

  await page.goto('/sync')
  await expect(page.getByRole('heading', { name: 'Recent runs' })).toBeVisible()
  await expect(page.getByRole('cell', { name: 'Completed' })).toBeVisible()
  await expect(page.getByRole('cell', { name: 'Failed' })).toBeVisible()
})

test('sync page has no detectable accessibility violations', async ({ page }) => {
  await page.route('**/api/sync/status*', async (route) => {
    await route.fulfill({
      json: { in_progress: false, last_error: null, latest_run: pipelineRun(), history: [pipelineRun()] },
    })
  })

  await page.goto('/sync')
  await expect(page.getByRole('heading', { name: 'Sync' })).toBeVisible()
  const results = await new AxeBuilder({ page }).analyze()
  expect(results.violations).toEqual([])
})
