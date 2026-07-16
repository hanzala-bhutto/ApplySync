# Langfuse LLM-judge prompts (reference copy)

Configured in Langfuse's own UI (Evaluators -> New evaluator), not files - this doc is a
version-controlled reference copy so the prompts aren't only living in Langfuse's Postgres.
See docs/feasibility/langfuse-llm-judge.md for the design rationale.

All three: **Target = manual/backfill only** (Actions -> Evaluate on selected traces), never live.

## 1. Relevance Judge
- **Target data**: Observations named `scrutinize_relevance`
- **Score**: `relevance_correct` (Boolean)
- **Judge prompt**:

```
You are checking whether a job-application-tracking pipeline correctly decided
if an email is relevant (an actual application confirmation/status update) or
not (a job alert digest, newsletter, unrelated email).

Email being classified:
{{input}}

Pipeline's decision:
{{output}}

Is this decision correct? Respond with your reasoning, then a final verdict.
```

## 2. Extraction Judge
- **Target data**: Observations named `classify_and_extract`
- **Score**: `extraction_correct` (Boolean)
- **Judge prompt**:

```
You are checking whether a job-application-tracking pipeline correctly
extracted company name, job title, and status (applied/viewed/assessment/
interview/offer/rejected/other) from an application-related email.

Email:
{{input}}

Pipeline's extraction:
{{output}}

Check each field against the email content. Default to "applied" is only
correct if the email doesn't unambiguously state a later stage - a neutral
"we'll review your application" email is NOT evidence of interview/rejection.
Respond with your reasoning, then a final verdict.
```

## 3. Disambiguation Judge
- **Target data**: Observations named `disambiguate_match`
- **Score**: `disambiguation_correct` (Boolean)
- **Judge prompt**:

```
You are checking whether an agent correctly decided if a new job-application
email is a status update for an EXISTING application on record, a genuinely
DIFFERENT role at the same company, or a redundant DUPLICATE.

New email + candidate applications it was compared against:
{{input}}

Agent's verdict and reasoning:
{{output}}

A missing job title alone is not proof of a different application - check
whether the agent's stated reasoning is actually supported by the evidence
it gathered (status history / source email content), not just plausible-
sounding. Respond with your reasoning, then a final verdict.
```
