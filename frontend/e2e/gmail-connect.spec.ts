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
  await expect(connectLink).toHaveAttribute('href', /\/api\/gmail\/connect\?return_to=/)
})
