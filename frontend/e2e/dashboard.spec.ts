import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { mockApi } from './fixtures'

test.beforeEach(async ({ page }) => {
  await mockApi(page)
})

test('dashboard renders the pipeline board with cards grouped by status', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Pipeline' })).toBeVisible()
  await expect(page.getByRole('button', { name: /Acme Corp, Senior Backend/ })).toBeVisible()
  await expect(page.getByRole('button', { name: /Globex/ })).toBeVisible()
  await expect(page.getByRole('button', { name: /Initech/ })).toBeVisible()
})

test('changing a filter keeps the board visible instead of showing a full loading state', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('button', { name: /Acme Corp, Senior Backend/ })).toBeVisible()

  // Delay the second dashboard response so we can observe the in-between
  // state - this is the regression this test guards against: without
  // `placeholderData: keepPreviousData`, the board unmounts to "Loading..."
  // on every filter change.
  await page.route('**/api/dashboard*', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 300))
    await route.fulfill({
      json: {
        board: { applied: [], viewed: [], assessment: [], interview: [], offer: [], rejected: [], other: [] },
        status_order: ['applied', 'viewed', 'assessment', 'interview', 'offer', 'rejected', 'other'],
        breakdown: [],
        reminders: [],
        filter_options: { years: [2025, 2026], platforms: ['linkedin'], statuses: ['applied'] },
      },
    })
  })

  await page.getByLabel('Filter by year').selectOption('2025')

  // The old board (and its cards) must still be visible immediately after
  // triggering the filter change, before the new (empty) response resolves.
  await expect(page.getByRole('button', { name: /Acme Corp, Senior Backend/ })).toBeVisible()
  await expect(page.getByText('Loading...')).toHaveCount(0)
})

test('reminders section links to the stale application', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: /applied 2026-01-15, no update since/ }).click()
  await expect(page).toHaveURL(/\/applications\/1/)
})

test('dashboard has no detectable accessibility violations', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Pipeline' })).toBeVisible()
  const results = await new AxeBuilder({ page }).analyze()
  expect(results.violations).toEqual([])
})
