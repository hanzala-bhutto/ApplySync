from __future__ import annotations

import logging

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import HumanMessage
from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel, Field

from applysync.search import SearchResult, SearxngClient, SearxngError

logger = logging.getLogger(__name__)


class CompanyProfileResult(BaseModel):
    """A company profile synthesized STRICTLY from provided web-search results.

    Flat, scalar fields only, all optional. Two hard-won constraints baked in
    here (verified against the real Nemotron model, not just unit tests):

    1. NO nested/list fields. `ChatNVIDIA.with_structured_output` (tool-calling
       under the hood) silently returns an all-empty object for this model once
       the schema contains a list, even `list[str]`. So `recent_news` is a
       single free-text field, not a list of items.
    2. This is parsed via `PydanticOutputParser` off the model's plain text
       output, NOT `with_structured_output`. The same model that returns empty
       via tool-calling produces a complete, correct profile in plain text.
       See research_company below.

    Fields the search results do not support are left null rather than guessed:
    the point of grounding in SearXNG output is a verifiable profile (see the
    stored source_urls), not confident hallucination.
    """

    summary: str | None = Field(
        default=None, description="One or two plain sentences on what the company does."
    )
    industry: str | None = Field(default=None, description="Primary industry or sector.")
    company_size: str | None = Field(
        default=None, description="Employee count or size band (e.g. '501-1000', 'startup')."
    )
    headquarters: str | None = Field(default=None, description="Headquarters city and/or country.")
    website: str | None = Field(default=None, description="The company's own website URL.")
    recent_news: str | None = Field(
        default=None,
        description="A brief note on any recent news (funding, launches, layoffs, "
        "acquisitions) mentioned in the results. Null if none.",
    )


class ResearchError(RuntimeError):
    """Raised when a company profile cannot be produced (search failed, or the
    model output could not be parsed)."""


_PARSER = PydanticOutputParser(pydantic_object=CompanyProfileResult)

_RESEARCH_PROMPT = """You are compiling a short, factual profile of a company \
for a job applicant, using ONLY the web search results provided below. Do not \
use any prior knowledge, and do not invent facts. Fill each field from the \
results; if the results genuinely do not mention something, use null for that \
field only.

Company to profile: {company}

Search results:
{results}

{format_instructions}
"""


def _format_results(results: list[SearchResult]) -> str:
    lines = []
    for i, r in enumerate(results, start=1):
        lines.append(f"[{i}] {r.title}\n    url: {r.url}\n    {r.content}")
    return "\n".join(lines)


def research_company(
    display_name: str,
    *,
    search_client: SearxngClient,
    model,
    max_results: int = 6,
) -> tuple[CompanyProfileResult, list[str]]:
    """Search the web for a company and synthesize a grounded profile.

    Returns the profile plus the source URLs it was grounded in, so the caller
    can store them for human verification. Raises ResearchError if the search
    or the parse fails - the caller (the API endpoint) turns that into a clear
    error rather than a fabricated profile.
    """
    try:
        results = search_client.search(f"{display_name} company", max_results=max_results)
    except SearxngError as exc:
        raise ResearchError(f"web search failed: {exc}") from exc

    if not results:
        # No grounding material: return an empty profile rather than inventing
        # one. The caller still caches it so we don't hammer a dead query.
        return CompanyProfileResult(), []

    prompt = _RESEARCH_PROMPT.format(
        company=display_name,
        results=_format_results(results),
        format_instructions=_PARSER.get_format_instructions(),
    )
    try:
        # with_retry is LangChain's backoff for transient API errors (the free
        # tier is a shared pool). PydanticOutputParser reads the model's plain
        # text output - see CompanyProfileResult's docstring for why we don't
        # use with_structured_output here.
        response = model.with_retry(stop_after_attempt=5, wait_exponential_jitter=True).invoke(
            [HumanMessage(content=prompt)]
        )
        profile = _PARSER.parse(response.content)
    except OutputParserException as exc:
        logger.exception("Company research parse failed for %r", display_name)
        raise ResearchError(f"could not parse profile: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - any LLM/transport failure is a research failure
        logger.exception("Company research LLM call failed for %r", display_name)
        raise ResearchError(f"profile synthesis failed: {exc}") from exc

    source_urls = [r.url for r in results]
    return profile, source_urls
