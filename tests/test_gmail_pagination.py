import base64

from applysync.gmail.client import GmailClient


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _message(message_id: str) -> dict:
    return {
        "id": message_id,
        "threadId": f"thread-{message_id}",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "jobs@example.com"},
                {"name": "Subject", "value": "Your application"},
                {"name": "Date", "value": "Wed, 1 Jan 2026 09:00:00 +0000"},
            ],
            "body": {"data": _b64("body")},
        },
    }


class FakeMessagesResource:
    """Mimics Gmail API's users().messages(), paginating a fixed set of ids
    across list() calls the way the real API does with nextPageToken.
    """

    def __init__(self, all_ids: list[str], page_size: int = 2, fail_ids: set[str] | None = None):
        self._all_ids = all_ids
        self._page_size = page_size
        self._fail_ids = fail_ids or set()
        self.list_call_count = 0
        self.get_call_count = 0

    def list(self, userId, q, maxResults, pageToken=None):
        self.list_call_count += 1
        start = int(pageToken) if pageToken else 0
        page = self._all_ids[start : start + min(self._page_size, maxResults)]
        next_start = start + len(page)
        response = {"messages": [{"id": i} for i in page]}
        if next_start < len(self._all_ids):
            response["nextPageToken"] = str(next_start)
        return _Executable(response)

    def get(self, userId, id, format):
        self.get_call_count += 1
        if id in self._fail_ids:
            return _FailingExecutable(id)
        return _Executable(_message(id))


class _Executable:
    def __init__(self, value):
        self._value = value

    def execute(self):
        return self._value


class _FailingExecutable:
    def __init__(self, message_id: str):
        self._message_id = message_id

    def execute(self):
        raise RuntimeError(f"simulated transient failure fetching {self._message_id}")


class FakeGmailService:
    def __init__(self, all_ids: list[str], page_size: int = 2, fail_ids: set[str] | None = None):
        self.messages_resource = FakeMessagesResource(all_ids, page_size=page_size, fail_ids=fail_ids)

    def users(self):
        return self

    def messages(self):
        return self.messages_resource


def _client_with_fake_service(
    all_ids: list[str], page_size: int = 2, fail_ids: set[str] | None = None
) -> GmailClient:
    client = GmailClient.__new__(GmailClient)  # bypass __init__, no real settings/OAuth needed
    fake_service = FakeGmailService(all_ids, page_size=page_size, fail_ids=fail_ids)
    client._service = fake_service
    return client


def test_fetch_messages_follows_pagination_past_first_page():
    all_ids = [f"msg-{i}" for i in range(7)]
    client = _client_with_fake_service(all_ids, page_size=2)

    emails = client.fetch_messages("query", max_results=500)

    assert [e.message_id for e in emails] == all_ids
    assert client.service.messages_resource.list_call_count == 4  # ceil(7/2)


def test_fetch_messages_respects_max_results_cap_across_pages():
    all_ids = [f"msg-{i}" for i in range(20)]
    client = _client_with_fake_service(all_ids, page_size=5)

    emails = client.fetch_messages("query", max_results=8)

    assert len(emails) == 8
    assert [e.message_id for e in emails] == all_ids[:8]


def test_fetch_messages_returns_all_when_fewer_than_max_results():
    all_ids = [f"msg-{i}" for i in range(3)]
    client = _client_with_fake_service(all_ids, page_size=2)

    emails = client.fetch_messages("query", max_results=500)

    assert len(emails) == 3


def test_fetch_messages_fetches_bodies_concurrently_preserving_order():
    all_ids = [f"msg-{i}" for i in range(20)]
    client = _client_with_fake_service(all_ids, page_size=5)

    emails = client.fetch_messages("query", max_results=500)

    assert [e.message_id for e in emails] == all_ids
    assert client.service.messages_resource.get_call_count == 20


def test_fetch_messages_skips_failed_fetch_without_aborting_batch():
    all_ids = [f"msg-{i}" for i in range(10)]
    client = _client_with_fake_service(all_ids, page_size=5, fail_ids={"msg-4"})

    emails = client.fetch_messages("query", max_results=500)

    returned_ids = {e.message_id for e in emails}
    assert returned_ids == set(all_ids) - {"msg-4"}
    assert len(emails) == 9
