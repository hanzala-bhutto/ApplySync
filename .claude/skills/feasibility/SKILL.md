---
name: feasibility
description: Write (or update) a short feasibility report for an ApplySync feature - a Motivation/Problem/Solution/Changes/Benefits note on why the feature earns its place. Use before implementing any feature, or when the user asks for a feasibility report or invokes /feasibility.
---

# Feature feasibility report skill

Every feature in this project gets a short feasibility report answering **why
it earns its place** - not how it's built. The "how" lives in the plan file and
the PR body; this file must not drift into a design dump. Keep it short: one
line per heading, whole report under ~15 lines.

## Output target

One file per feature: `docs/feasibility/<feature-slug>.md`, kebab-case slug
matching the feature (e.g. `entity-resolution.md`, `company-research-card.md`,
`web-search.md`). Update the existing file if the feature already has one;
don't create a second.

## Template (exactly these five headings, in this order)

```markdown
# <Feature name>

## Motivation
<The underlying itch/context, one sentence.>

## Problem
<What's missing or broken without it, one sentence.>

## Solution
<What we build, one or two sentences.>

## Changes
<The files/modules/tables it touches, a short list.>

## Benefits
<The payoff, one or two bullets.>
```

## Discipline

- Ground it in the real need. Pull the motivation and constraints from
  `CLAUDE.md` (the "What this is", "Hard constraints", "Web search", and
  milestone sections), don't invent a justification.
- No gold-plating. If a feature's real reason is thin, say so plainly - a short
  honest report is the point, and it's a signal the feature may not earn its
  place.
- One report per feature, written before/at the start of implementing it, so
  the reasoning is captured while it's fresh.
- No em dashes anywhere (project-wide rule).

## Worked example (`docs/feasibility/web-search.md`)

```markdown
# Web search (self-hosted SearXNG)

## Motivation
The tracker should do more than passively read the inbox - the richer
web-research features (company research, follow-up drafting, entity resolution)
all need live web results.

## Problem
There was no way to fetch live web data, and a paid search API or external
account would break the local-first, keyless design the rest of the tool holds to.

## Solution
A self-hosted SearXNG instance exposing a JSON API on localhost, fronted by a
thin httpx client (`SearxngClient`) behind the same DI pattern as the Gmail/LLM
clients so features and tests inject a fake.

## Changes
- `searxng/` (docker-compose.yml + settings.yml, no Redis)
- `backend/applysync/search/client.py` (`SearxngClient`, `get_search_client`)
- `applysync search "<query>"` CLI smoke test

## Benefits
- Live web grounding with no API key, account, or per-call cost.
- One reusable, mockable client every web-research feature builds on.
```
