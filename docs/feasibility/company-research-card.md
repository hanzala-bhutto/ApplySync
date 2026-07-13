# Company research card

## Motivation
When looking at an application, the user often wants a quick, factual read on the company without leaving the dashboard to go search themselves.

## Problem
The dashboard only showed what the confirmation email stated; there was no company context (what they do, size, HQ, recent news), and hand-searching every company is tedious.

## Solution
`POST /api/applications/{id}/research` searches SearXNG and synthesizes a grounded profile via `PydanticOutputParser` (flat scalar schema, since this model returns empty from `with_structured_output` on list fields), cached per company and shown on a clearly web-labeled detail-page card with its source URLs.

## Changes
- `backend/applysync/research/company.py` (grounded synthesis)
- `CompanyProfile` table + research API endpoint
- `ResearchCard`/`ResearchResult` in `ApplicationDetail.tsx`

## Benefits
- One-click, source-linked company context in place.
- Web-sourced data stays in its own table and card, never mixed with email-extracted facts (the project's core data-integrity rule).
