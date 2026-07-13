# Web search (self-hosted SearXNG)

## Motivation
The tracker should do more than passively read the inbox; the richer web-research features (company research, follow-up drafting, entity resolution) all need live web results.

## Problem
There was no way to fetch live web data, and a paid search API or external account would break the local-first, keyless design the rest of the tool holds to.

## Solution
A self-hosted SearXNG instance exposing a JSON API on localhost, fronted by a thin httpx client (`SearxngClient`) behind the same DI pattern as the Gmail/LLM clients so features and tests inject a fake.

## Changes
- `searxng/` (docker-compose.yml + settings.yml, no Redis)
- `backend/applysync/search/client.py` (`SearxngClient`, `get_search_client`, `SearxngError`)
- `applysync search "<query>"` CLI smoke test

## Benefits
- Live web grounding with no API key, account, or per-call cost.
- One reusable, mockable client every later web-research feature builds on.
