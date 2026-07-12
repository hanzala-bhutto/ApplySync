"""Shared fake LLM objects for testing at the model boundary
(model.invoke / model.with_structured_output(...).invoke), never a real API call.
"""


class FakeResponse:
    def __init__(self, content: str):
        self.content = content


class FakeStructuredModel:
    def __init__(self, result=None, exception=None):
        self._result = result
        self._exception = exception

    def invoke(self, messages):
        if self._exception is not None:
            raise self._exception
        return self._result

    def with_retry(self, **kwargs):
        return self


class FakeExtractModel:
    def __init__(self, structured_model: FakeStructuredModel):
        self._structured_model = structured_model

    def with_structured_output(self, schema):
        return self._structured_model

    def with_retry(self, **kwargs):
        return self


class FakeCompletionModel:
    """Stand-in for a chat model used via plain-text completion + an output
    parser (model.with_retry(...).invoke(messages).content), as
    research_company does. `content` should be the raw text the parser will
    parse (e.g. a JSON string)."""

    def __init__(self, content: str = "", exception=None):
        self._content = content
        self._exception = exception

    def with_retry(self, **kwargs):
        return self

    def invoke(self, messages):
        if self._exception is not None:
            raise self._exception
        return FakeResponse(self._content)


class FakeSearchClient:
    """Stand-in for SearxngClient: returns canned results or raises, never
    touches a real SearXNG instance."""

    def __init__(self, results=None, exception=None):
        self._results = results or []
        self._exception = exception

    def search(self, query, *, max_results=5, **kwargs):
        if self._exception is not None:
            raise self._exception
        return self._results[:max_results]


class FakeGmailService:
    def __init__(self, raw_message: dict):
        self._raw_message = raw_message

    def users(self):
        return self

    def messages(self):
        return self

    def get(self, userId, id, format):
        return self

    def execute(self):
        return self._raw_message


class FakeGmailClient:
    def __init__(self, raw_message: dict):
        self.service = FakeGmailService(raw_message)
        self._raw_message = raw_message

    def get_message(self, message_id: str):
        from applysync.gmail.client import parse_message

        return parse_message(self._raw_message)
