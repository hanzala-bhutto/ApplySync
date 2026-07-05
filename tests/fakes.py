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


class FakeChatModel:
    """Mocks classify_relevant's model.invoke(...)."""

    def __init__(self, content: str):
        self._content = content

    def invoke(self, messages):
        return FakeResponse(self._content)

    def with_retry(self, **kwargs):
        return self


class FakeCombinedModel:
    """A single fake object standing in for the one real model instance the
    graph passes to both make_classify_node and make_extract_node.
    """

    def __init__(self, classify_content: str, extract_result=None, extract_exception=None):
        self._classify_content = classify_content
        self._extract_result = extract_result
        self._extract_exception = extract_exception

    def invoke(self, messages):
        return FakeResponse(self._classify_content)

    def with_retry(self, **kwargs):
        return self

    def with_structured_output(self, schema):
        return FakeStructuredModel(result=self._extract_result, exception=self._extract_exception)
