---
name: docs
description: Regenerate or update this project's documentation (Motivation, Features, Architecture, Data Flow, Setup, Roadmap) from the current code state. Use when the user asks to write/update docs, README, or architecture diagrams for the ApplySync project.
---

# Project documentation skill

Keep documentation synced to actual code, not to the plan or to memory. Read
the real files before writing a word. If a described feature/node/table
doesn't exist yet in the code, don't document it as if it does; put it under
Roadmap instead.

## Output target

Update (or create) `README.md` at the project root, with these sections in
this order. If the project has grown a `docs/` folder with split files, follow
that structure instead, check before assuming a single README.

1. **Motivation**: one paragraph: the manual-folder-tracking problem, the
   multi-platform fragmentation, why email-driven LLM extraction instead of
   scraping/manual entry. Pull this from `CLAUDE.md`, don't reinvent it.
2. **Features**: bullet list generated from what's *actually implemented*.
   Check `backend/applysync/` for which pieces exist: Gmail ingestion? pipeline
   nodes? dashboard views? scheduler? Only list what runs today.
3. **Architecture**: the pipeline diagram (Gmail to LangGraph nodes to SQLite
   to dashboard). Copy/adapt the ASCII diagram in `CLAUDE.md` if it still matches
   `pipeline/graph.py`'s actual node wiring; if the code has diverged, redraw
   it from the real graph definition, not from the diagram.
4. **Data Flow**: narrative walk of one email through the system: fetched,
   classified, extracted, matched, persisted, shown on dashboard. Ground
   each step in the actual function name in `pipeline/nodes.py`.
5. **Setup**: how to install deps, set up `.env` from `.env.example`, run
   Gmail OAuth (point to `/gmail-setup` skill rather than duplicating steps),
   initialize the DB, run the CLI (`applysync sync`, `applysync serve`).
6. **Roadmap**: milestone checklist copied from `CLAUDE.md`'s milestone
   section, kept in sync with it (update both when a milestone completes).

## Process

1. Read `CLAUDE.md` for the durable architecture/constraints summary.
2. Read the actual source tree (`backend/applysync/**`) to confirm what exists
   vs. what's still planned.
3. Read the existing `README.md` if present. Preserve any user-written
   sections that aren't part of the fixed structure above (e.g. a personal
   note, screenshots) rather than clobbering them.
4. Write the update. Don't add sections beyond the six above unless the user
   asks for them.
5. If milestone status changed, update the checklist in `CLAUDE.md` too so the
   two files don't drift apart.
