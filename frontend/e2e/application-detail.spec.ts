import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { mockApi } from './fixtures'

test.beforeEach(async ({ page }) => {
  await mockApi(page)
})

test('detail page shows application info and timeline', async ({ page }) => {
  await page.goto('/applications/1')
  await expect(page.getByRole('heading', { name: 'Acme Corp' })).toBeVisible()
  await expect(page.getByText('Senior Backend Engineer')).toBeVisible()
  await expect(page.getByRole('cell', { name: 'applied' })).toBeVisible()
})

test('status badge and status select are visually decoupled (regression: weird select colors)', async ({ page }) => {
  await page.goto('/applications/1')
  const badge = page.getByText('applied', { exact: true }).first()
  await expect(badge).toBeVisible()

  const select = page.getByLabel('Change status')
  // The select must not carry the status-color background classes - it's
  // plain-styled on purpose so open/closed states look consistent, per the
  // "Colors are bit wierd" bug fix.
  await expect(select).toHaveClass(/bg-white/)
  await expect(select).not.toHaveClass(/bg-emerald|bg-rose|bg-amber|bg-sky/)
})

test('changing status via the select updates the badge', async ({ page }) => {
  await page.goto('/applications/1')
  await page.getByLabel('Change status').selectOption('interview')
  await expect(page.getByText('Status set to interview')).toBeVisible()
})

test('edit form toggles and saves fields', async ({ page }) => {
  await page.goto('/applications/1')
  await page.getByRole('button', { name: 'Edit' }).click()
  const companyInput = page.getByLabel('Company')
  await companyInput.fill('Acme Corporation')
  await page.getByRole('button', { name: 'Save' }).click()
  await expect(page.getByText('Application updated.')).toBeVisible()
})

test('reprocess is gated behind a centered confirm dialog', async ({ page }) => {
  await page.goto('/applications/1')
  await page.getByRole('button', { name: 'Reprocess from email' }).click()

  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await expect(dialog.getByText('Reprocess from email?')).toBeVisible()

  // Regression check for the off-center dialog bug: fixed + inset-0 + m-auto
  // must be present so native <dialog> centering isn't broken by Tailwind's
  // preflight margin reset.
  await expect(dialog).toHaveClass(/inset-0/)
  await expect(dialog).toHaveClass(/m-auto/)

  await dialog.getByRole('button', { name: 'Reprocess', exact: true }).click()
  await expect(page.getByText('Re-extracted from the original email.')).toBeVisible()
})

test('reprocess confirm dialog can be cancelled without side effects', async ({ page }) => {
  await page.goto('/applications/1')
  await page.getByRole('button', { name: 'Reprocess from email' }).click()
  await page.getByRole('button', { name: 'Cancel' }).click()
  await expect(page.getByRole('dialog')).toBeHidden()
})

test('timeline row reveals the original source email for verification', async ({ page }) => {
  await page.goto('/applications/1')
  await page.getByRole('button', { name: 'View email' }).click()
  await expect(page.getByText('noreply@acme.example')).toBeVisible()
  await expect(page.getByText(/Thanks for applying to the Senior Backend Engineer role/)).toBeVisible()

  await page.getByRole('button', { name: 'Hide email' }).click()
  await expect(page.getByText('noreply@acme.example')).toBeHidden()
})

test('detail page has no detectable accessibility violations', async ({ page }) => {
  await page.goto('/applications/1')
  await expect(page.getByRole('heading', { name: 'Acme Corp' })).toBeVisible()
  const results = await new AxeBuilder({ page }).analyze()
  expect(results.violations).toEqual([])
})
