# ApplySync

An email-driven job application tracker that pulls your applications out of your inbox and into one place, no matter which platform or company you applied through.

## Table of Contents

- [Introduction](#introduction)
- [Tech Stack](#tech-stack)
- [Features](#features)
- [Architecture](#architecture)
- [Getting Started](#getting-started)
  - [Prerequisites](#prerequisites)
  - [Installation](#installation)
  - [Usage](#usage)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [License](#license)
- [Acknowledgments](#acknowledgments)

## Introduction

Job hunting across LinkedIn, Indeed, StepStone, direct company career pages, and whatever new AI recruiting tool shows up this month means your application history ends up scattered across a dozen inboxes and a pile of manually named folders. There is no single place that answers a simple question: which companies have I actually applied to, and what happened next?

ApplySync starts from an observation: almost every application you submit generates a confirmation or status email somewhere, whether from LinkedIn, an ATS vendor like SmartRecruiters or Personio, or the company itself. Instead of building a scraper for every platform (a losing battle the moment any of them changes their HTML), ApplySync reads those emails directly and uses an LLM to pull out the structured facts: company, role, status, platform. New platforms and ATS vendors need a config change, not new code.

This project is also a deliberate learning vehicle. It is being built hands on to learn LangChain, LangChain Community, LangGraph, LangSmith, Langfuse, and the broader practice of agentic pipelines and LLMOps, by shipping something the author actually uses every day to track a real job search rather than a toy demo.

## Tech Stack

- **Language**: Python 3.11+
- **LLM orchestration**: [LangChain](https://www.langchain.com/) and [LangGraph](https://www.langchain.com/langgraph) (structured output extraction, a stateful graph with conditional routing, and SQLite checkpointing)
- **LLM provider**: [NVIDIA NIM](https://build.nvidia.com/) via `langchain-nvidia-ai-endpoints`, running `nvidia/nemotron-3-ultra-550b-a55b`
- **Email ingestion**: Gmail API (readonly scope) via `google-api-python-client`
- **Persistence**: SQLite via [SQLModel](https://sqlmodel.tiangolo.com/)
- **CLI**: [Typer](https://typer.tiangolo.com/)
- **Dashboard** (in progress): [FastAPI](https://fastapi.tiangolo.com/) + Jinja2 + HTMX
- **Scheduler** (planned): APScheduler
- **Observability** (planned, phase 2): LangSmith and Langfuse

## Features

What is actually working today:

- **Gmail ingestion** with a readonly OAuth flow, never write or send scopes
- **Platform-agnostic search**: application confirmations are found by subject phrase ("thank you for applying", "application received", "bewerbung", and more), not by a hardcoded sender allowlist, so ATS vendors and direct company emails that were never explicitly added still get picked up
- **A LangGraph extraction pipeline**: classify whether an email is a genuine application confirmation, extract structured details (company, job title, status, location, salary, URL) with an LLM, match it against existing applications, and persist it
- **Idempotent processing**: every email is tracked by Gmail message id so scheduled re-runs never reprocess or duplicate the same email
- **Status tracking across the application lifecycle**: applied, viewed, interview, rejected, offer are all recognized statuses, correctly extracted even when the subject line is misleadingly polite (a rejection email that opens with "thank you for your application" is still correctly read as a rejection)
- **Best-effort platform attribution** for dashboard labeling (LinkedIn, Indeed, StepStone, SmartRecruiters, Personio, Ashby, and more), configured entirely in `config/sources.yaml`

Not built yet, see [Roadmap](#roadmap): the web dashboard, scheduled automation, and observability/evals.

## Architecture

```
Gmail API --(poll, keyword-filtered query)--> gmail/client.py
                                                    |
                                         raw email batch
                                                    v
                          LangGraph pipeline: pipeline/graph.py
     classify_relevant -> extract_structured_data -> match_existing_application -> upsert_db
                                                    |
                                                    v
                              SQLite: db/models.py + repository.py
                                                    |
                                                    v
                          Web dashboard (planned): FastAPI + Jinja2 + HTMX
```

Each email is run through the graph individually. An email that is not a genuine application confirmation, or one the LLM cannot confidently extract from, is routed to a short-circuit path that marks it processed without creating any data, so it is recorded once and never retried.

## Getting Started

### Prerequisites

- Python 3.11 or newer
- A Gmail account you apply for jobs from
- A Google Cloud project with the Gmail API enabled and OAuth credentials (see the one-time setup below)
- A free [NVIDIA API key](https://build.nvidia.com/) for the LLM calls

### Installation

Clone the repository:

```
git clone https://github.com/hanzala-bhutto/ApplySync.git
cd ApplySync
```

Create a virtual environment and install the project:

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

Complete the one-time Gmail OAuth setup (Google Cloud project, consent screen, `credentials.json`) using the walkthrough in `.claude/skills/gmail-setup/SKILL.md`, then place `credentials.json` at the path `.env` points to.

### Usage

Run one pass of the ingestion and extraction pipeline:

```
applysync sync
```

This fetches new application-related emails from Gmail, classifies and extracts structured data from each with an LLM, and persists the results to a local SQLite database.

The web dashboard (`applysync serve`) is not implemented yet, see [Roadmap](#roadmap).

## Roadmap

- [x] Gmail OAuth client, keyword-based query builder, message parsing
- [x] LangGraph pipeline (classify, extract, match, upsert) with SQLite persistence and idempotency
- [ ] Web dashboard: status board, per-application timeline, by-platform breakdown, follow-up reminders, and a "Connect Gmail" button in the browser
- [ ] Scheduler: automatic periodic syncing
- [ ] Observability: LangSmith and Langfuse tracing, plus a hand-labeled evaluation set for extraction accuracy

Full milestone detail lives in `CLAUDE.md`.

## Contributing

This started as a personal tool and learning project, but issues and pull requests are welcome. If you are adding support for a new job platform or ATS vendor, it almost certainly belongs in `config/sources.yaml`, not as new parsing code, that separation is a deliberate design constraint, see `CLAUDE.md` for why.

## License

Released under the [MIT License](LICENSE).

## Acknowledgments

- [NVIDIA](https://build.nvidia.com/) for free access to the Nemotron model family used for extraction
- The [LangChain and LangGraph](https://www.langchain.com/) teams and community
- Built with the help of [Claude Code](https://claude.com/claude-code) as a pair-programming and learning partner throughout
