# Title-less email disambiguation bias

## Motivation
The disambiguation agent should not guess blindly when a status email names no job title at all.

## Problem
A real bug: a title-less status email ("Your Interview Appointment") was attached to the wrong candidate application, with the model hallucinating the interviewer's own title as if it were the job. Title-less emails give the agent nothing to match on, so it free-guessed instead of reasoning from real evidence.

## Solution
When the extracted job title is the `UNSPECIFIED_JOB_TITLE` sentinel, add explicit prompt guidance: (1) an instruction never to invent a job title from unrelated details, (2) a weak prior naming the single most-recently-updated still-open (non-rejected/declined) candidate, to be confirmed or contradicted with the tools rather than accepted on recency alone, and (3) an explicit fallback to `different_application` when no single candidate can be tied to the email with concrete evidence - a spurious extra row is easy to spot and merge later, a wrong merge silently hides a real application.

## Changes
- `backend/applysync/research/disambiguate.py`: `_title_less_guidance`, wired into the system prompt only when the new email's title is the unspecified sentinel
- `tests/fakes.py`: `FakeToolLoopModel` now records `seen_messages` so tests can inspect the prompt actually sent
- `tests/test_disambiguate.py`: three new tests (single active candidate biases toward it, no active candidate pushes toward separate, a normal titled email gets no guidance at all)

## Benefits
- Closes the exact gap that caused the real Rabot mis-merge, without touching the normal (titled) code path at all.
- Keeps the same fail-safe posture as the rest of the agent: prefer a recoverable extra row over an unrecoverable wrong merge.
