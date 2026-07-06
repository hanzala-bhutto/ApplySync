import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { mockApi } from './fixtures'

function suggestion(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: 1,
    message_id: 'msg-1',
    application_id: null,
    action: 'new_application',
    previous_classification: 'irrelevant',
    suggested_classification: 'relevant',
    previous_extract_json: null,
    suggested_extract_json: '{"company_name": "Acme", "job_title": "Engineer", "status": "applied"}',
    status: 'pending',
    pipeline_run_id: 'run-1',
    created_at: '2026-01-15T10:00:00Z',
    reviewed_at: null,
    ...overrides,
  }
}

test.beforeEach(async ({ page }) => {
  await mockApi(page)
})

test('review page shows a message when nothing is pending', async ({ page }) => {
  await page.goto('/review')
  await expect(page.getByRole('heading', { name: 'Review' })).toBeVisible()
  await expect(page.getByText('Nothing to review right now.')).toBeVisible()
})

test('review page shows a new-application suggestion with its suggested fields', async ({ page }) => {
  await page.route('**/api/review-suggestions*', async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({ json: [suggestion()] })
      return
    }
    await route.continue()
  })

  await page.goto('/review')
  await expect(page.getByText('New application')).toBeVisible()
  await expect(page.getByText('Acme')).toBeVisible()
  await expect(page.getByText('Engineer')).toBeVisible()
})

test('review page shows a before/after diff for an update-existing suggestion', async ({ page }) => {
  await page.route('**/api/review-suggestions*', async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({
        json: [
          suggestion({
            action: 'update_existing',
            application_id: 5,
            previous_extract_json: '{"company_name": "Acme", "job_title": "Engineer", "status": "applied"}',
            suggested_extract_json: '{"company_name": "Acme", "job_title": "Engineer", "status": "rejected"}',
          }),
        ],
      })
      return
    }
    await route.continue()
  })

  await page.goto('/review')
  await expect(page.getByText('Update existing application')).toBeVisible()
  await expect(page.getByText('applied', { exact: true })).toBeVisible()
  await expect(page.getByText('rejected', { exact: true })).toBeVisible()
})

test('approving a suggestion removes it from the list and shows a toast', async ({ page }) => {
  let approved = false
  await page.route('**/api/review-suggestions*', async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({ json: approved ? [] : [suggestion()] })
      return
    }
    await route.continue()
  })
  await page.route('**/api/review-suggestions/1/approve', async (route) => {
    approved = true
    await route.fulfill({ json: suggestion({ status: 'approved' }) })
  })

  await page.goto('/review')
  await expect(page.getByText('New application')).toBeVisible()

  await page.getByRole('button', { name: 'Approve' }).click()

  await expect(page.getByText('Suggestion approved.')).toBeVisible()
  await expect(page.getByText('Nothing to review right now.')).toBeVisible()
})

test('rejecting a suggestion removes it from the list and shows a toast', async ({ page }) => {
  let rejected = false
  await page.route('**/api/review-suggestions*', async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({ json: rejected ? [] : [suggestion()] })
      return
    }
    await route.continue()
  })
  await page.route('**/api/review-suggestions/1/reject', async (route) => {
    rejected = true
    await route.fulfill({ json: suggestion({ status: 'rejected' }) })
  })

  await page.goto('/review')
  await page.getByRole('button', { name: 'Reject' }).click()

  await expect(page.getByText('Suggestion dismissed.')).toBeVisible()
  await expect(page.getByText('Nothing to review right now.')).toBeVisible()
})

test('review page has no detectable accessibility violations', async ({ page }) => {
  await page.route('**/api/review-suggestions*', async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({ json: [suggestion(), suggestion({ id: 2, action: 'reclassify_irrelevant' })] })
      return
    }
    await route.continue()
  })

  await page.goto('/review')
  await expect(page.getByRole('heading', { name: 'Review' })).toBeVisible()
  const results = await new AxeBuilder({ page }).analyze()
  expect(results.violations).toEqual([])
})
