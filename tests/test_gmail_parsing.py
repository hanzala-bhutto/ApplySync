import base64

from applysync.gmail.client import parse_message


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def test_parse_message_extracts_headers_and_plain_body():
    message = {
        "id": "msg-1",
        "threadId": "thread-1",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "jobs-noreply@linkedin.com"},
                {"name": "Subject", "value": "Your application was sent to Acme Corp"},
                {"name": "Date", "value": "Mon, 1 Jan 2026 10:00:00 +0000"},
            ],
            "body": {"data": _b64("Your application for Backend Engineer was sent.")},
        },
    }

    email = parse_message(message)

    assert email.message_id == "msg-1"
    assert email.thread_id == "thread-1"
    assert email.sender == "jobs-noreply@linkedin.com"
    assert email.subject == "Your application was sent to Acme Corp"
    assert "Backend Engineer" in email.body


def test_parse_message_finds_plain_text_part_in_multipart_message():
    message = {
        "id": "msg-2",
        "threadId": "thread-2",
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "From", "value": "no-reply@indeed.com"},
                {"name": "Subject", "value": "Application submitted"},
                {"name": "Date", "value": "Tue, 2 Jan 2026 10:00:00 +0000"},
            ],
            "parts": [
                {"mimeType": "text/html", "body": {"data": _b64("<p>html version</p>")}},
                {"mimeType": "text/plain", "body": {"data": _b64("plain text version")}},
            ],
        },
    }

    email = parse_message(message)

    assert email.body == "plain text version"


def test_parse_message_falls_back_to_html_when_no_plain_text_part():
    message = {
        "id": "msg-4",
        "threadId": "thread-4",
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "From", "value": "jack@jackandjill.ai"},
                {"name": "Subject", "value": "Application drafts for Acme"},
                {"name": "Date", "value": "Wed, 3 Jul 2026 10:00:00 +0000"},
            ],
            "parts": [
                {
                    "mimeType": "text/html",
                    "headers": [{"name": "Content-Type", "value": "text/html; charset=UTF-8"}],
                    "body": {"data": _b64("<p>Hi <b>there</b>,</p><p>Application sent.</p>")},
                }
            ],
        },
    }

    email = parse_message(message)

    assert email.body == "Hi there , Application sent."


def test_parse_message_decodes_non_utf8_charset():
    # 0x92 is Windows-1252's curly right-single-quote; invalid/undefined in UTF-8,
    # which is exactly the mojibake ("We�re") seen against real StepStone emails.
    raw_bytes = b"We" + bytes([0x92]) + b"re keeping our fingers crossed"
    encoded = base64.urlsafe_b64encode(raw_bytes).decode("ascii").rstrip("=")

    message = {
        "id": "msg-5",
        "threadId": "thread-5",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "info@email.stepstone.de"},
                {"name": "Subject", "value": "Your application has been sent"},
                {"name": "Date", "value": "Wed, 1 Jul 2026 09:00:00 +0000"},
                {"name": "Content-Type", "value": "text/plain; charset=windows-1252"},
            ],
            "body": {"data": encoded},
        },
    }

    email = parse_message(message)

    assert email.body == "We’re keeping our fingers crossed"


def test_parse_message_handles_missing_headers_gracefully():
    message = {
        "id": "msg-3",
        "threadId": "thread-3",
        "payload": {"mimeType": "text/plain", "headers": [], "body": {}},
    }

    email = parse_message(message)

    assert email.sender == ""
    assert email.subject == ""
    assert email.body == ""
