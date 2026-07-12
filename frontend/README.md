# ApplySync frontend

The React (Vite + TypeScript) dashboard for ApplySync. It runs as a separate dev
server and talks to the FastAPI backend over its JSON API. See the repo root
`README.md` and `CLAUDE.md` for the full project.

## Develop

```bash
npm install
npm run dev          # starts Vite (prints a localhost URL; open it)
```

The dev server expects the backend running on `http://127.0.0.1:8000`
(`applysync serve` from the repo root). Override with `VITE_API_BASE_URL` in
`.env.local` if the backend is elsewhere. For the company research card to
return anything, the SearXNG container also has to be up (`docker compose up -d`
in `../searxng`).

Other scripts:

```bash
npm run build        # tsc -b && vite build (production build into dist/)
npm run preview      # serve the built dist/ locally
npm run lint         # oxlint
```

## End-to-end tests (Playwright)

The suite lives in `e2e/`. Every `/api/*` call is **mocked** (`e2e/fixtures.ts`),
so the tests never touch the real backend or a real Gmail-derived database, and
Playwright's `webServer` builds/serves the app for the test run only (it is not a
persistent dev server). One-time browser install if you have never run it:

```bash
npx playwright install chromium
```

### Run them

```bash
npm run test:e2e            # headless - fastest, nothing visible (CI default)
npm run test:e2e:ui        # interactive UI mode - watch + time-travel (best for seeing the UI)
npm run test:e2e:headed    # runs in a real, visible browser window
npm run test:e2e:report    # open the HTML report from the last run
```

For stepping through a single spec live in the Inspector:

```bash
npx playwright test application-detail.spec.ts --debug
```

### Seeing the pages (the `about:blank` gotcha)

In **UI mode** (`npm run test:e2e:ui`) the center pane shows a **DOM snapshot for
the action you have selected**, not a live browser. Until you pick an action it
shows `about:blank` - that is expected, not a failure. To see a page:

1. Click a test in the left list, then hit the **play** button to run it.
2. In the **Actions** panel (bottom-left), click an action such as
   `page.goto(...)` or an `expect(...)` - the pane renders the page exactly as it
   looked at that step. Scrub through the actions to watch it change.

If you want to watch a live browser instead of snapshots, use
`npm run test:e2e:headed` or `--debug`.

If a page is still blank after selecting an action, the preview server served a
stale bundle (`vite preview` serves `dist/` and does not rebuild first). Rebuild
and re-run:

```bash
npm run build && npm run test:e2e:ui
```

### What you are looking at

The tests render **mocked fixture data** (e.g. "Acme Corp", "Globex"), not your
real applications. To see the dashboard with your real data, run the actual app
(`applysync serve` + `npm run dev`), not Playwright.
