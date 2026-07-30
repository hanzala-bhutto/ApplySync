---
name: run-stack
description: Give the exact commands to bring up ApplySync's full local stack (SearXNG, Langfuse, backend API, frontend dashboard) each in its own terminal. Use when the user wants to run/start the whole app, not just one piece of it.
---

# Running the full ApplySync stack

This project's dev servers are **never started or backgrounded via Claude
Code's own tools** (`CLAUDE.md` hard constraint - Windows ghost-listener pain
on ports 8000/5173 from a past session). This skill's job is to hand the user
the exact commands for each piece, in the order to run them, each in its own
terminal window they own. Don't run any of the long-running commands below
yourself; a one-off check (`curl`, `applysync search "..."` as a smoke test)
is fine.

## Pieces and order

Not every piece is required every time - pick based on what the user needs.

1. **SearXNG** (optional - only needed for web-research features: company
   research card, entity resolution, future follow-up/dossier features).
   Docker must already be running.
   ```
   cd searxng
   docker compose up -d
   ```
   Detached/Docker-managed, so this one command is enough - no terminal needs
   to stay open for it. Verify: `applysync search "egym careers"`.

2. **Langfuse** (optional - only needed for tracing/observability). Also
   Docker-managed and detached.
   ```
   cd langfuse
   docker compose up -d
   ```
   First run only: open `http://localhost:3000`, sign up locally, create a
   project, copy its API keys into the root `.env`
   (`LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`/`LANGFUSE_HOST`). Tracing is
   diagnostic only - a missing/misconfigured instance never blocks a sync.

3. **Backend API** (required for the dashboard). Give the user this to run
   in its own terminal, with the venv activated:
   ```
   applysync serve --reload
   ```
   Serves the FastAPI JSON API + triggers syncs from the dashboard.

4. **Frontend dashboard** (required for the dashboard). Separate terminal:
   ```
   cd frontend
   npm run dev
   ```
   Runs the Vite dev server as its own process, by design (not unified
   single-command serving - an explicit project choice).

## What to tell the user

Lay out only the pieces relevant to what they asked for, in the order above,
each as a command to paste into its own terminal (steps 1-2 are detached and
don't need a dedicated terminal left open; steps 3-4 do). Don't just print a
block of commands with no explanation - name which terminal/piece each one
is, and mention any first-run setup (Langfuse API keys, Gmail OAuth via
`.claude/skills/gmail-setup/SKILL.md`) if it looks like this is a fresh
machine.

## Verifying it's up

- Backend: `curl http://localhost:8000/api/applications` (or whatever port
  `applysync serve` reports) should return JSON, not a connection error.
- Frontend: Vite prints the local URL (typically `http://localhost:5173`) to
  its own terminal on startup.
- SearXNG: `http://localhost:8888`.
- Langfuse: `http://localhost:3000`.

If something looks broken, check first whether a stale process is already
holding the port (the exact ghost-listener failure mode this skill's
no-backgrounding rule exists to avoid) before assuming it's an app bug.
