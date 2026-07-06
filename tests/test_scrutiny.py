import pytest

from applysync.gmail.models import RawEmail
from applysync.pipeline.nodes import _heuristic_scrutinize


def _email(subject="", body=""):
    return RawEmail(
        message_id="msg-1",
        thread_id="thread-1",
        sender="jobs@example.com",
        subject=subject,
        date="Wed, 1 Jul 2026 09:00:00 +0000",
        body=body,
    )


@pytest.mark.parametrize(
    "subject",
    [
        "Thank you for your application at Acme",
        "Thank you for applying to Acme",
        "Your application for Software Engineer",
        "Application received",
        "Bewerbung eingegangen",
    ],
)
def test_heuristic_scrutinize_passes_narrow_confirmation_phrases(subject):
    assert _heuristic_scrutinize(_email(subject=subject)) == "pass"


@pytest.mark.parametrize(
    "subject",
    [
        "New jobs matching Software Engineer",
        "Jobs for you this week",
        "Recommended jobs based on your profile",
        "Weekly digest: jobs you might like",
        "Boost your chances of landing a job",
    ],
)
def test_heuristic_scrutinize_rejects_digest_markers(subject):
    assert _heuristic_scrutinize(_email(subject=subject)) == "reject"


@pytest.mark.parametrize(
    "subject",
    [
        "Your application was sent",
        "Update on your application",
        "Interview scheduled",
        "We regret to inform you",
    ],
)
def test_heuristic_scrutinize_ambiguous_for_broadened_single_word_matches(subject):
    assert _heuristic_scrutinize(_email(subject=subject)) == "ambiguous"


def test_heuristic_scrutinize_checks_body_too_for_reject_markers():
    email = _email(subject="Weekly update", body="Check out these similar jobs you might like!")
    assert _heuristic_scrutinize(email) == "reject"
