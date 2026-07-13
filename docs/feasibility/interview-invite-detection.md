# Interview-invite detection

## Motivation
An interview invitation is the most important status change in a job hunt, but these emails were slipping through untracked so the dashboard never showed the user reaching the interview stage.

## Problem
Two stacked gaps: (1) the Gmail query matches on `subject:` keywords only, and interview invites are phrased "Meeting invite", "Invitation to a first conversation", or are calendar invites - none contain a confirmation keyword, so they are never fetched; (2) even when fetched, the classifier's `interview` status required a "live interview/call", so a first-round "conversation" was scored as `applied`.

## Solution
Add a curated list of specific, multi-word interview-invitation phrases (`invitation_phrases` in `sources.yaml`) searched across the whole email (not subject-restricted) so real invites get fetched, with `scrutinize_relevance` filtering the extra noise; and broaden the classifier's `interview` definition to count a first-round call / introductory conversation about the role.

## Changes
- `backend/config/sources.yaml` + `config.py` (`SourcesConfig.invitation_phrases`)
- `gmail/query_builder.py` (full-text phrases alongside `subject:` keywords)
- `pipeline/nodes.py` (`_CLASSIFY_AND_EXTRACT_PROMPT` interview definition)

## Benefits
- Genuine interview invitations (e.g. Agileday) now get fetched and correctly marked `interview`.
- Phrases are specific multi-word strings, so flood stays low and the existing scrutiny node absorbs the rest.
- Known limit: recruiter cold-outreach that never names an employer (a search agency's "let's chat") stays untracked, since there is no company/role to record - a separate product decision, noted not silently dropped.
