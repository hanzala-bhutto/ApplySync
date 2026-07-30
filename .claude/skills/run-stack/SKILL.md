---
name: run-stack
description: Actually launch ApplySync's full local stack (SearXNG, Langfuse, backend API, frontend dashboard) as tracked background tasks. Use when the user wants to run/start/spin up the whole app, not just get the commands for it.
---

# Running the full ApplySync stack

**This skill is a sanctioned, explicit exception to CLAUDE.md's general
"don't background dev servers" rule**, agreed with the user after they
confirmed they want `/run-stack` to actually start the stack, not just print
commands. The original rule exists because of real Windows ghost-listener
pain (orphaned processes still holding ports 8000/5173 with no tracked
process behind them). This skill mitigates that risk instead of ignoring it:
every long-running process it starts MUST go through the Bash tool's
`run_in_background`, never a raw detached/nohup spawn, so it stays tracked
and stoppable. Do not use this launch method anywhere else in the project;
outside this skill, the original hard constraint (hand the user commands,
let them run it themselves) still applies.

## 0. Pre-flight: check for stale listeners first

Before starting anything, check whether ports 8000 (backend) and 5173
(frontend) are already in use:

```
netstat -ano | grep -E ":8000 |:5173 "
```

If a port is already listening, do NOT start a second instance on it - that's
exactly how a ghost listener gets created (two processes racing for one
port). Tell the user what's already there and ask whether to reuse it, or
have them kill it first, before proceeding.

## Pieces and order

Not every piece is required every time - ask or infer from context which
ones are actually needed (frontend work needs the frontend; a Gmail-only
pipeline change needs just the backend; research features need SearXNG).

1. **SearXNG** (optional - web-research features only). Docker must be
   running. This one is detached and Docker-managed already, safe to run
   directly (not via `run_in_background`, it returns immediately):
   ```
   cd searxng && docker compose up -d
   ```

2. **Langfuse** (optional - tracing/observability only). Same story, Docker
   detaches it on its own:
   ```
   cd langfuse && docker compose up -d
   ```
   First run only: open `http://localhost:3000`, sign up locally, create a
   project, put its API keys in the root `.env`.

3. **Backend API** (needed for the dashboard). Launch via Bash tool with
   `run_in_background: true` so it's tracked, not orphaned:
   ```
   applysync serve --reload
   ```
   (venv must be active in that shell - activate it as part of the same
   background command if it isn't already, e.g.
   `.venv\Scripts\activate && applysync serve --reload`.)

4. **Frontend dashboard** (needed for the dashboard). Also
   `run_in_background: true`:
   ```
   cd frontend && npm run dev
   ```

## After launching

- Report the task IDs/handles for the backend and frontend background tasks
  back to the user so they know these exist and how to check on them.
- Verify each piece actually came up (don't just assume the background task
  starting means the server is ready):
  - Backend: `curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/applications` (with a short `--max-time`), expect a real HTTP status, not a connection failure.
  - Frontend: check the background task's output for the Vite "Local:" URL line.
  - SearXNG: `http://localhost:8888`.
  - Langfuse: `http://localhost:3000`.
- Tell the user explicitly these are now running as tracked background
  tasks in this session, and that closing/ending the session (or an explicit
  stop request) is what should stop them - don't leave this ambiguous.

## Stopping the stack

Stop the backend/frontend background tasks explicitly (via the task-stop
tool) when the user asks to stop the stack, or before starting a fresh copy
of the same piece - never start a second instance on the same port. `docker
compose down` in `searxng/`/`langfuse/` for those two if the user wants them
fully stopped (otherwise they're fine left running detached).

## If something looks broken

Check first whether a stale process is already holding the port before
assuming it's an app bug - the pre-flight check in step 0 exists precisely
to catch this before it happens, but re-check it if something looks wrong
mid-session.
