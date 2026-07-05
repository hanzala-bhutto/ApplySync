import { test, expect } from '@playwright/test'
import { mockApi } from './fixtures'

test('connection banner is hidden when Gmail is already connected', async ({ page }) => {
  await mockApi(page)
  await page.goto('/')
  await expect(page.getByRole('link', { name: 'Connect Gmail' })).toHaveCount(0)
})

test('connection banner prompts to connect when Gmail is not connected', async ({ page }) => {
  await mockApi(page)
  await page.route('**/api/gmail/status*', async (route) => {
    await route.fulfill({ json: { connected: false } })
  })

  await page.goto('/')

  const connectLink = page.getByRole('link', { name: 'Connect Gmail' })
  await expect(connectLink).toBeVisible()
  const href = await connectLink.getAttribute('href')
  const returnTo = new URL(href!).searchParams.get('return_to')
  // Regression check: return_to must be an absolute URL (with the
  // frontend's own origin). A relative path here means the backend's
  // eventual RedirectResponse resolves against the BACKEND's origin, not
  // the frontend's, sending the user to e.g. http://127.0.0.1:8001/?gmail=
  // connected (a 404) instead of back to the dashboard.
  expect(returnTo).toMatch(/^https?:\/\//)
})
