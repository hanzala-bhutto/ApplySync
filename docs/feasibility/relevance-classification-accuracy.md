# Relevance-classification accuracy and tiered escalation model

## Motivation
The eval harness's first real baseline (150 verified real emails) surfaced a severe, previously-invisible bug: 79.8% classification accuracy, with every single false negative a rejection email wrongly marked irrelevant.

## Problem
`classify_and_extract` systematically marked rejection emails (English and German) as `is_relevant: False`, silently dropping roughly a quarter of real application outcomes from every sync - worse than a duplicate row, since it leaves the application looking correctly "applied" forever. A second, smaller bug: two Wolters Kluwer confirmation emails were rejected by the scrutiny heuristic because an unrelated reject-marker substring ("job alert") appeared in unrelated footer navigation text, checked before the (correct) confirmation-phrase match. A handful of smaller extraction gaps also surfaced: no placeholder defense for `company_name` (the literal string "unknown" leaking through), a UI button label ("Join our Talent Pool") bleeding into `job_title`, and gender/diversity qualifiers like "(m/f/d)" fragmenting otherwise-identical titles across ATS templates.

## Solution
1. Rewrote STEP 1 of the extraction prompt to explicitly separate "was a job application submitted and this concerns it" (is_relevant) from "is the news good or bad" (status) - a rejection is now explicitly called out as just as relevant as an acceptance.
2. Reordered the scrutiny heuristic so a narrow, high-precision confirmation-phrase match wins over an incidental reject-marker match found elsewhere in the email, and extended the confirmation check to the body prefix, not just the subject.
3. Added `_normalize_company_name` (mirroring the existing job-title placeholder defense) and extended the job-title placeholder set and `_normalize_for_matching`'s gender-qualifier stripping.
4. Two further prompt clarifications from real false positives found while re-measuring: explicitly scope `is_relevant` to job applications only (a rental-lease-extension email was wrongly marked relevant on the generic word "application"), and explicitly exclude a recruiting intermediary talking about itself with no confirmed real employer.
5. **Tiered escalation model**: `settings.llm_escalation_model` (the larger, slower model this project ran before switching to nano for speed) now handles the scrutiny stage's rare ambiguous-case call directly, and gets one retry with the same prompt when `classify_and_extract`'s fast-model call either fails outright or returns a relevant result with no usable company name. Narrow and mechanical - no confidence self-reporting asked of the model - so the vast majority of emails still cost exactly one fast, cheap call.

One prompt change was tried and reverted after measurement: telling the model to exclude trailing ATS reference numbers from job_title caused it to over-generalize into truncating real title content after any qualifier or dash ("Python Backend Engineer - Remote" -> "Python Backend Engineer"), a net accuracy regression caught only by re-running the eval - directly validating why this harness exists.

## Changes
- `backend/applysync/pipeline/nodes.py`: STEP 1 rewrite, `_normalize_company_name`, extended placeholder set, scrutiny heuristic reordering, `escalation_model` param on both node factories
- `backend/applysync/db/repository.py`: `_GENDER_QUALIFIER_RE` in `_normalize_for_matching`
- `backend/applysync/llm.py`, `backend/applysync/config.py`: `get_chat_model(model_name=...)` override, `settings.llm_escalation_model`
- `backend/applysync/pipeline/graph.py`: `escalation_model` threaded through `build_graph`/`compile_graph`/`process_emails`/`run_sync`
- `eval/run_eval.py`: constructs and wires the escalation model, `--no-escalation` to isolate its effect
- 14 new/updated regression tests

## Benefits
- Verified against the real eval baseline, not just unit tests with fakes: scrutiny false-rejects 1.9% -> 0.0%, classification accuracy 79.8% -> 98.2%, status 92.9% -> 95.3%, company steady at 95.3%. Every number here is measured, not estimated.
- The escalation path is narrow and cheap: only the emails that already fail deterministic checks pay the larger model's latency, so the 40 RPM budget stays dominated by the fast model as before.
