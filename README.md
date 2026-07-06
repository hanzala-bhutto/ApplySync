# ApplySync

An email-driven job application tracker that pulls your applications out of your inbox and into one place, no matter which platform or company you applied through.

## Table of Contents

- [Motivation](#motivation)
- [Tech Stack](#tech-stack)
- [Features](#features)
- [Architecture](#architecture)
- [Data Flow](#data-flow)
- [Setup](#setup)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [License](#license)
- [Acknowledgments](#acknowledgments)

## Motivation

Job hunting across LinkedIn, Indeed, StepStone, direct company career pages, and whatever new AI recruiting tool shows up this month means your application history ends up scattered across a dozen inboxes and a pile of manually named folders. There is no single place that answers a simple question: which companies have I actually applied to, and what happened next? ApplySync starts from an observation: almost every application you submit generates a confirmation or status email somewhere, whether from LinkedIn, an ATS vendor like SmartRecruiters or Personio, or the company itself. Instead of building a scraper for every platform (a losing battle the moment any of them changes their HTML), ApplySync reads those emails directly and uses an LLM to pull out the structured facts: company, role, status, platform. New platforms and ATS vendors need a config change, not new code. This project is also a deliberate learning vehicle: it is being built hands-on to learn LangChain, LangChain Community, LangGraph, LangSmith, Langfuse, and the broader practice of agentic/multi-agent orchestration, by shipping something the author actually uses every day to track a real job search rather than a toy demo.

## Tech Stack

- **Language**: Python 3.11+
- **LLM orchestration**: [LangChain](https://www.langchain.com/) and [LangGraph](https://www.langchain.com/langgraph) - structured-output extraction, a stateful per-email graph with conditional routing, and SQLite checkpointing
- **LLM provider**: [NVIDIA NIM](https://build.nvidia.com/) via `langchain-nvidia-ai-endpoints`, running `nvidia/nemotron-3-nano-30b-a3b` with reasoning disabled for speed, plus a client-side rate limiter matched to the free tier's 40 requests/minute cap
- **Email ingestion**: Gmail API (readonly scope only) via `google-api-python-client`, concurrent per-message fetch via a thread pool
- **Persistence**: SQLite via [SQLModel](https://sqlmodel.tiangolo.com/)
- **API**: [FastAPI](https://fastapi.tiangolo.com/), explicit Pydantic response models (real, useful `/docs` Swagger UI)
- **Frontend**: React (Vite + TypeScript) + Tailwind + Framer Motion + `@dnd-kit`, a separate dev server calling the FastAPI JSON API
- **Scheduler**: none yet in-process - see [Roadmap](#roadmap)
- **Observability**: none yet - see [Roadmap](#roadmap)

## Features

What is actually working today:

- **Gmail ingestion** with a readonly OAuth flow (never write or send scopes), either via the CLI's first-run consent or a "Connect Gmail" button in the dashboard that walks through Google's consent screen and back
- **Platform-agnostic, keyword-driven search**: application-related emails are found by subject phrase and keyword (`config/sources.yaml`'s `confirmation_keywords`), not a hardcoded sender allowlist, so ATS vendors and direct company emails that were never explicitly added still get picked up
- **Concurrent Gmail fetch**: per-message bodies are fetched with a worker thread pool rather than one at a time
- **A LangGraph extraction pipeline**, one email per graph invocation:
  - `scrutinize_relevance`: a hybrid heuristic + cheap-LLM filter that rejects job-alert digests and recommendation emails before they ever reach the expensive extraction call
  - `classify_and_extract`: one merged LLM call classifies relevance and extracts structured fields (company, job title, status, location, salary, URL)
  - `match_existing_application`: heuristic company/title/platform matching (normalized for case, whitespace, and legal suffixes like "SE"/"GmbH"/"Inc") decides new vs. update vs. duplicate
  - `upsert_db`: deterministic persistence, no LLM involved
- **Idempotent processing**: every email is tracked by Gmail message id so re-runs never reprocess or duplicate the same email; a run's incremental progress (emails scrutinized/extracted/written) is persisted as it happens, not just once the run finishes
- **Status tracking across the full application lifecycle**: applied, viewed, assessment, interview, rejected, offer, declined (manual-only, for offers you turn down yourself), and other
- **A React dashboard**: a status-board (Kanban) view with drag-and-drop status correction (keyboard-operable via `@dnd-kit`), inline field editing, a "reprocess from source email" action, per-application timelines with the original source email viewable inline, follow-up reminders, and a per-platform response-rate breakdown - all served by a FastAPI JSON API with a full OpenAPI schema
- **Manual "Sync Now"** button and a dedicated `/sync` page with a staged progress view (ingestion/scrutiny/extraction/write) and recent-run history, plus the equivalent `applysync sync` CLI command
- **Best-effort platform attribution** for dashboard labeling (LinkedIn, Indeed, StepStone, SmartRecruiters, Personio, Ashby, and more), configured entirely in `config/sources.yaml`
- **Playwright end-to-end tests** with an `@axe-core/playwright` accessibility check on every page

Not built yet, see [Roadmap](#roadmap): automatic/scheduled syncing and observability tracing/evals.

## Architecture

```
[Gmail API] --(poll, keyword-filtered query, concurrent fetch)--> gmail/client.py
                                                                          |
                                                                raw email batch
                                                                          v
                                    LangGraph pipeline: pipeline/graph.py (one email per invocation)
   scrutinize_relevance -> classify_and_extract -> match_existing_application -> upsert_db
        (heuristic + cheap LLM)   (merged classify+extract call)
                                                                          |
                                                                          v
                                                    SQLite: db/models.py + repository.py
                                                                          |
                                                                          v
                                                     FastAPI JSON API (web/api.py, /api/*)
                                                                          |
                                                                          v
                                                    React frontend (frontend/, separate dev server)

              Manual trigger: POST /api/sync -> background thread runs the pipeline once
              [Not yet built] Scheduler: an OS-level scheduled task -> `applysync sync` daily
              [Not yet built] LangSmith / Langfuse tracing wraps the LangGraph run
```

`fetch_emails` is a plain batch fetch, not a graph node - the graph operates on one email at a time, driven by a loop in `process_emails`. An email that fails scrutiny, isn't a genuine application confirmation, or can't be confidently extracted is routed to a short-circuit terminal node that marks it processed without creating any application/event rows, so it is recorded once and never retried, while the reason it was skipped is kept.

## Data Flow

Following one email through the system, function by function:

1. `run_sync` (`pipeline/graph.py`) builds a Gmail search query from `config/sources.yaml`'s keywords, bounded by the last successful run's date (with a small lookback buffer), and calls `GmailClient.fetch_messages` (`gmail/client.py`) to pull the raw batch concurrently.
2. `process_emails` filters out anything already in the `processed_emails` table (the idempotency guard), then invokes the compiled LangGraph once per remaining email via `compiled.stream(...)`.
3. `scrutinize_relevance` (`pipeline/nodes.py`) runs a heuristic first (instant reject on known digest markers, instant pass on the original narrow confirmation phrases); only a genuinely ambiguous email triggers one cheap `RelevanceOnlyResult` LLM call. A reject routes straight to `mark_scrutiny_rejected` and the email is marked processed without further work.
4. `classify_and_extract` sends the email through one structured-output LLM call (`ClassifyAndExtractResult`) that both classifies relevance and extracts `company_name`, `job_title`, `status`, `job_url`, `location`, and `salary_text` in a single round trip.
5. `match_existing_application` normalizes company name and job title (case, whitespace, legal suffixes) and looks for an existing `Application` row with the same company/title/platform, deciding `new_application`, `update_existing`, or `duplicate_skip`.
6. `upsert_db` writes the `Application` row (if new) or a new `StatusEvent` (if updating), always finishing by calling `mark_processed` so the email is never re-ingested.
7. The FastAPI layer (`web/api.py`) exposes the result as `/api/dashboard`, `/api/applications/{id}`, `/api/reminders`, etc.; the React dashboard (`frontend/`) renders the status board, timeline, and reminders from those endpoints, and can trigger corrections (drag-and-drop status change, inline edit, reprocess-from-source-email) that write back through the same API.

## Setup

### Prerequisites

- Python 3.11 or newer, and Node.js (for the frontend) if you want the dashboard UI
- A Gmail account you apply for jobs from
- A Google Cloud project with the Gmail API enabled and OAuth credentials
- A free [NVIDIA API key](https://build.nvidia.com/) for the LLM calls

### Installation

Clone the repository:

```
git clone https://github.com/hanzala-bhutto/ApplySync.git
cd ApplySync
```

Create a virtual environment and install the backend:

```
python -m venv .venv
.venv\Scripts\activate      # Windows
source .venv/bin/activate   # macOS / Linux
pip install -e ".[dev]"
```

Copy the environment template and fill it in:

```
cp .env.example .env
```

At minimum, set `NVIDIA_API_KEY` in `.env`.

Complete the one-time Gmail OAuth setup (Google Cloud project, consent screen, `credentials.json`) using the walkthrough in `.claude/skills/gmail-setup/SKILL.md`, then place `credentials.json` at the path `.env` points to - or skip this entirely and use the dashboard's "Connect Gmail" button instead, which walks through the same consent flow in the browser.

If you want the dashboard UI, install the frontend separately:

```
cd frontend
npm install
```

### Usage

Run one pass of the ingestion and extraction pipeline from the CLI:

```
applysync sync
```

Or run the API server and trigger syncs from the dashboard's "Sync Now" button / `/sync` page instead:

```
applysync serve
```

Run the frontend in its own terminal (separate dev server, by design):

```
cd frontend
npm run dev
```

Both `applysync sync` and a dashboard-triggered sync fetch new application-related emails from Gmail, run them through the LangGraph pipeline, and persist the results to a local SQLite database (`applysync.db` by default).

## Roadmap

- [x] Gmail OAuth client (CLI first-run and in-dashboard web flow), keyword-based query builder, concurrent message fetch
- [x] LangGraph pipeline: scrutiny, classify+extract, match, upsert, with SQLite persistence, idempotency, and incremental progress tracking
- [x] React dashboard: status board with drag-and-drop, per-application timeline with source-email verification, inline editing, reprocess action, follow-up reminders, per-platform analytics, and a staged sync-progress page
- [x] Pipeline redesign (broadened keyword coverage, a scrutiny node ahead of extraction, and staged sync progress) - real-inbox verification of the broadened filter is still outstanding
- [ ] Scheduler: automatic periodic syncing independent of whether the dashboard/server is running (planned as an OS-level scheduled task, not an in-process one)
- [ ] Observability: LangSmith and Langfuse tracing, plus a hand-labeled evaluation set for extraction accuracy

Full milestone detail, including the reasoning behind each decision, lives in `CLAUDE.md`.

## Contributing

This started as a personal tool and learning project, but issues and pull requests are welcome. If you are adding support for a new job platform or ATS vendor, it almost certainly belongs in `config/sources.yaml`, not as new parsing code - that separation is a deliberate design constraint, see `CLAUDE.md` for why.

## License

Released under the [MIT License](LICENSE).

## Acknowledgments

- [NVIDIA](https://build.nvidia.com/) for free access to the Nemotron model family used for extraction
- The [LangChain and LangGraph](https://www.langchain.com/) teams and community
- Built with the help of [Claude Code](https://claude.com/claude-code) as a pair-programming and learning partner throughout
