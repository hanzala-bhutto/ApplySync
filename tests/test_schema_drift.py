"""Schema-drift guard for the LLM classifier's output space.

This is the LLM-specific check normal CI lacks: a prompt/schema edit is a
production deploy, and silently widening or renaming the classifier's status
set changes what every downstream stage (matching, Kanban columns, the eval
metrics) can ever receive. Locking the set here forces any such change to be
deliberate - the test fails loudly, the developer updates the frozen set on
purpose, and that reminds them to re-run the eval gate.

Deterministic, no model call, so it runs in cloud CI (see .github/workflows/
ci.yml), unlike eval/run_eval.py which needs the real model + PII gold data.
"""
from __future__ import annotations

import typing

from applysync.pipeline.state import ClassifyAndExtractResult

# The classifier's allowed statuses. `declined` is intentionally NOT here: it is
# a manual-only user action, never something the LLM may emit (see the declined
# status milestone in CLAUDE.md). Changing this set is a real behavior change -
# update it deliberately and re-run the eval gate.
FROZEN_LLM_STATUSES = {
    "applied",
    "viewed",
    "assessment",
    "interview",
    "rejected",
    "offer",
    "other",
}


def _literal_values(annotation) -> set[str]:
    values: set[str] = set()
    for arg in typing.get_args(annotation):
        # Optional[Literal[...]] -> (Literal[...], NoneType); recurse to reach
        # the string literals regardless of nesting/union order.
        values.update(typing.get_args(arg))
        if isinstance(arg, str):
            values.add(arg)
    return {v for v in values if isinstance(v, str)}


def test_classifier_status_space_is_frozen():
    annotation = ClassifyAndExtractResult.model_fields["status"].annotation
    assert _literal_values(annotation) == FROZEN_LLM_STATUSES
