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


class FakeAIResponse:
    """An AIMessage-like object for the tool-loop fakes: only the attributes the
    disambiguation agent loop reads (.content, .tool_calls). tool_calls is a list
    of {"name", "args", "id"} dicts, matching LangChain's normalized shape."""

    def __init__(self, content: str = "", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class FakeToolLoopModel:
    """Stand-in for a chat model driven as a bind_tools agent loop
    (model.bind_tools(tools).with_retry(...).invoke(messages)). Returns a
    scripted sequence of FakeAIResponse objects, one per invoke - so a test can
    script "call this tool, then submit the verdict"."""

    def __init__(self, script):
        self._script = list(script)
        self.invocations = 0

    def bind_tools(self, tools):
        return self

    def with_retry(self, **kwargs):
        return self

    def invoke(self, messages):
        self.invocations += 1
        if not self._script:
            raise AssertionError("FakeToolLoopModel ran out of scripted responses")
        return self._script.pop(0)


class FakeExtractAndToolModel:
    """A single fake that both does structured classify+extract
    (with_structured_output, for classify_and_extract) AND drives a bind_tools
    agent loop (for the disambiguation branch), so one model can be threaded
    through the whole graph including the ambiguous-match route."""

    def __init__(self, structured_model, agent_script):
        self._structured = structured_model
        self._agent = FakeToolLoopModel(agent_script)

    def with_structured_output(self, schema):
        return self._structured

    def with_retry(self, **kwargs):
        return self

    def bind_tools(self, tools):
        return self._agent


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
