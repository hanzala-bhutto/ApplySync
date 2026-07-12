import { test } from '@playwright/test'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { mockApi } from './fixtures'

// Regenerates the screenshots shown in the root README from the mocked
// fixtures (so they use example data, never a real inbox). Skipped in a normal
// test run - regenerate with:
//   SCREENSHOTS=1 npx playwright test screenshots.spec.ts
const OUT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', 'docs', 'screenshots')

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
})
