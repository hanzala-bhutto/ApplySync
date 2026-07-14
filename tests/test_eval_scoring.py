"""Unit tests for the eval harness's scoring logic (pure functions, no LLM,
no DB) - the runner itself (eval/run_eval.py) hits the real model and is
exercised manually, but every metric definition is pinned down here."""

from applysync.evaluation import (
    EvalSample,
    StagePrediction,
    Thresholds,
    format_report,
    load_all_samples,
    load_samples,
    save_samples,
    score_samples,
)
from applysync.pipeline.nodes import UNSPECIFIED_JOB_TITLE


def _sample(message_id="m1", relevant=True, company="Acme", title="Engineer", status="applied"):
    return EvalSample(
        message_id=message_id,
        sender="jobs@acme.example",
        subject="Your application",
        date="Wed, 1 Jul 2026 09:00:00 +0000",
        body="body",
        label_is_relevant=relevant,
        label_company=company,
        label_title=title,
        label_status=status,
        verified=True,
    )


def _prediction(message_id="m1", scrutiny="pass", classification="relevant",
                company="Acme", title="Engineer", status="applied"):
    return StagePrediction(
        message_id=message_id,
        scrutiny=scrutiny,
        classification=classification,
        company=company,
        title=title,
        status=status,
    )


def test_fully_correct_sample_scores_perfect():
    report = score_samples([_sample()], {"m1": _prediction()})

    assert report.scrutiny_false_rejects == []
    assert report.classification_accuracy == 1.0
    assert report.company_accuracy == 1.0
    assert report.title_accuracy == 1.0
    assert report.status_accuracy == 1.0
    assert report.mismatches == []


def test_scrutiny_false_reject_counted_and_not_double_scored():
    """A relevant email rejected at scrutiny is the worst failure (silently
    dropped forever); it must show up in the false-reject list and must NOT
    also count against classification/extraction, which it never reached."""
    report = score_samples(
        [_sample()], {"m1": _prediction(scrutiny="reject", classification=None)}
    )

    assert report.scrutiny_false_rejects == ["m1"]
    assert report.scrutiny_false_reject_rate == 1.0
    assert report.classified_total == 0
    assert report.extraction_total == 0


def test_scrutiny_over_pass_on_irrelevant_is_informational_not_a_failure():
    """An irrelevant email that scrutiny lets through only costs one LLM
    call; classification catching it downstream is the system working."""
    sample = _sample(relevant=False, company=None, title=None, status=None)
    report = score_samples(
        [sample], {"m1": _prediction(classification="irrelevant", company=None, title=None, status=None)}
    )

    assert report.scrutiny_over_passes == 1
    assert report.classification_accuracy == 1.0
    assert report.extraction_total == 0  # irrelevant samples have no fields to extract


def test_company_compared_with_matching_normalization():
    """"EGYM SE" vs "EGYM" must score correct - the same legal-suffix
    normalization the matching layer applies, so the eval measures what
    actually matters downstream."""
    sample = _sample(company="EGYM")
    report = score_samples([sample], {"m1": _prediction(company="EGYM SE")})

    assert report.company_accuracy == 1.0


def test_null_title_label_expects_unspecified_sentinel():
    sample = _sample(title=None)

    correct = score_samples([sample], {"m1": _prediction(title=UNSPECIFIED_JOB_TITLE)})
    invented = score_samples([sample], {"m1": _prediction(title="Some Invented Role")})

    assert correct.title_accuracy == 1.0
    assert invented.title_accuracy == 0.0
    assert any(m.field == "job_title" for m in invented.mismatches)


def test_wrong_status_recorded_as_mismatch():
    report = score_samples([_sample(status="applied")], {"m1": _prediction(status="rejected")})

    assert report.status_accuracy == 0.0
    mismatch = next(m for m in report.mismatches if m.field == "status")
    assert mismatch.expected == "applied"
    assert mismatch.got == "rejected"


def test_misclassified_relevant_sample_skips_extraction_scoring():
    report = score_samples(
        [_sample()], {"m1": _prediction(classification="irrelevant", company=None, title=None, status=None)}
    )

    assert report.classification_accuracy == 0.0
    assert report.extraction_total == 0


def test_sample_without_prediction_is_skipped():
    report = score_samples([_sample()], {})

    assert report.total == 1
    assert report.classified_total == 0


def test_thresholds_fail_on_any_false_reject():
    report = score_samples(
        [_sample()], {"m1": _prediction(scrutiny="reject", classification=None)}
    )

    assert Thresholds().passed(report) is False


def test_thresholds_pass_on_perfect_report():
    report = score_samples([_sample()], {"m1": _prediction()})

    assert Thresholds().passed(report) is True


def test_format_report_renders_without_error():
    report = score_samples([_sample(status="applied")], {"m1": _prediction(status="rejected")})

    text = format_report(report, Thresholds())

    assert "status" in text
    assert "FAIL" in text


def test_sample_round_trips_through_dict():
    sample = _sample()
    assert EvalSample.from_dict(sample.to_dict()) == sample


def test_save_and_load_samples_round_trip_and_is_human_readable(tmp_path):
    """save_samples must write a pretty-printed JSON array (verified/labels
    before the long body in each record) that a human can review directly -
    not one-line-per-record JSONL, which was unreadable once a record holds
    a multi-paragraph email body."""
    path = tmp_path / "gold.json"
    verified = _sample(message_id="v1")
    unverified = EvalSample(
        message_id="u1", sender="a@b.com", subject="s", date="d", body="b",
        label_is_relevant=False, verified=False,
    )
    save_samples(path, [verified, unverified])

    text = path.read_text(encoding="utf-8")
    assert text.startswith("[\n")  # pretty-printed array, not one object per line
    assert '"verified": true' in text

    assert load_all_samples(path) == [verified, unverified]
    assert load_samples(path) == [verified]
    assert load_samples(path, include_unverified=True) == [verified, unverified]
