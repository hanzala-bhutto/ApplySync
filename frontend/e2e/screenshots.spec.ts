import { test } from '@playwright/test'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { mockApi } from './fixtures'

// Regenerates the screenshots shown in the root README from the mocked
// fixtures (so they use example data, never a real inbox). Skipped in a normal
// test run - regenerate with:
//   SCREENSHOTS=1 npx playwright test screenshots.spec.ts
const OUT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', 'docs', 'screenshots')

// A finished incremental run, so the Sync page shows populated stage bars and
// counts instead of the empty "no sync has run yet" state.
function pipelineRun() {
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
  }
}

test.describe('screenshots', () => {
  test.use({ viewport: { width: 1440, height: 900 } })

  test.beforeEach(async ({ page }) => {
    test.skip(!process.env.SCREENSHOTS, 'set SCREENSHOTS=1 to regenerate README screenshots')
    await mockApi(page)
  })

  test('dashboard', async ({ page }) => {
    await page.goto('/')
    await page.getByRole('button', { name: /Acme Corp, Senior Backend/ }).waitFor()
    await page.screenshot({ path: path.join(OUT, 'dashboard.png'), fullPage: true })
  })

  test('application detail with company research', async ({ page }) => {
    await page.goto('/applications/1')
    await page.getByRole('button', { name: /Research Acme Corp/ }).click()
    await page.getByText('Acme Corp builds industrial widgets').waitFor()
    await page.screenshot({ path: path.join(OUT, 'application-detail.png'), fullPage: true })
  })

  test('follow-up reminders', async ({ page }) => {
    await page.goto('/reminders')
    await page.getByText('Acme Corp').first().waitFor()
    await page.screenshot({ path: path.join(OUT, 'reminders.png'), fullPage: true })
  })

  test('analytics', async ({ page }) => {
    await page.goto('/analytics')
    await page.getByText(/linkedin/i).first().waitFor()
    await page.screenshot({ path: path.join(OUT, 'analytics.png'), fullPage: true })
  })

  test('sync progress', async ({ page }) => {
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
    await page.getByText('5 emails found').waitFor()
    await page.screenshot({ path: path.join(OUT, 'sync.png'), fullPage: true })
  })

  test('review suggestions', async ({ page }) => {
    await page.route('**/api/review-suggestions', async (route) => {
      if (route.request().method() === 'GET') {
        await route.fulfill({
          json: [
            {
              id: 1, message_id: 'msg-1', application_id: null, action: 'new_application',
              previous_classification: 'irrelevant', suggested_classification: 'relevant',
              previous_extract_json: null,
              suggested_extract_json: '{"company_name": "Globex", "job_title": "Platform Engineer", "status": "applied"}',
              status: 'pending', pipeline_run_id: 'run-1', created_at: '2026-01-15T10:00:00Z', reviewed_at: null,
            },
            {
              id: 2, message_id: 'msg-2', application_id: 5, action: 'update_existing',
              previous_classification: 'relevant', suggested_classification: 'relevant',
              previous_extract_json: '{"company_name": "Acme", "job_title": "Engineer", "status": "applied"}',
              suggested_extract_json: '{"company_name": "Acme", "job_title": "Engineer", "status": "rejected"}',
              status: 'pending', pipeline_run_id: 'run-1', created_at: '2026-01-15T10:00:00Z', reviewed_at: null,
            },
          ],
        })
        return
      }
      await route.continue()
    })
    await page.goto('/review')
    await page.getByText('New application').first().waitFor()
    await page.screenshot({ path: path.join(OUT, 'review.png'), fullPage: true })
  })
})
