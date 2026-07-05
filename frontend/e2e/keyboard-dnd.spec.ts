import { test, expect } from '@playwright/test'
import { mockApi } from './fixtures'

test.beforeEach(async ({ page }) => {
  await mockApi(page)
})

test('cards stay keyboard-operable: Enter opens, Space does not navigate', async ({ page }) => {
  await page.goto('/')
  const card = page.getByRole('button', { name: /Acme Corp, Senior Backend/ })
  await card.focus()
  await expect(card).toBeFocused()

  // Space is bound to drag start/end (see keyboardCodes override in
  // Dashboard.tsx) - it must NOT trigger navigation like a native button
  // click would by default.
  await page.keyboard.press('Space')
  await expect(page).toHaveURL('/')

  // Space again ends the drag (drop in place, no status change since there's
  // no valid drop target focused) - still no navigation.
  await page.keyboard.press('Space')
  await expect(page).toHaveURL('/')

  // Enter performs the normal button click and navigates to the detail page.
  await card.press('Enter')
  await expect(page).toHaveURL(/\/applications\/1/)
})
