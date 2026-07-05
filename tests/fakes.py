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
