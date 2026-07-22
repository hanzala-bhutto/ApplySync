from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(PROJECT_ROOT / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    llm_model: str = "nvidia/nemotron-3-nano-30b-a3b"
    # Used only for the rare escalation path (see nodes.py's classify_and_extract
    # retry and scrutinize_relevance's ambiguous-case call): the larger, slower
    # model this project ran before switching to nano for speed (see CLAUDE.md's
    # LLM section) - a known-good fallback for the small minority of emails the
    # fast model can't confidently handle, not something called per-email.
    llm_escalation_model: str = "nvidia/nemotron-3-ultra-550b-a55b"
    nvidia_api_key: str = ""
    # Optional: run the disambiguation agent on Groq (faster, separate rate
    # budget) with NVIDIA as the fallback. Active only when both are set.
    groq_api_key: str = ""
    groq_agent_model: str = ""
    # Confidence-routed merges (M5): the disambiguation agent's
    # same_application/duplicate verdict is applied automatically only when its
    # self-reported confidence is at or above this bar ("high"/"medium"/"low").
    # Below it, the email is written as a new application and a merge
    # ReviewSuggestion is queued for a human instead of merging silently. Default
    # "medium" routes only "low"-confidence merges to review; set "high" to also
    # review "medium" ones if wrong merges are seen.
    disambiguation_min_auto_merge_confidence: str = "medium"
    gmail_client_secrets_path: Path = Path(".secrets/credentials.json")
    gmail_token_path: Path = Path(".secrets/token.json")
    db_path: Path = Path("applysync.db")
    sync_interval_minutes: int = 20
    # Base URL of the self-hosted SearXNG instance (see searxng/docker-compose.yml)
    # that powers the web-research features. Local, keyless, no external account.
    searxng_url: str = "http://localhost:8888"

    # Self-hosted Langfuse (see langfuse/docker-compose.yml), NOT LangSmith - a
    # hosted SaaS would ship email bodies off-machine, contradicting the
    # local-first design. Tracing is a no-op whenever the keys are unset, so
    # this is never required to run the pipeline (unit tests, degraded runs).
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "http://localhost:3000"

    # Relative paths in .env (the default for all three above) must resolve
    # against the project root, not whatever directory the process happens
    # to be started from - confirmed as a real bug: the Gmail OAuth web flow
    # (unlike the CLI, always run from repo root by convention) hit "No
    # Gmail client secrets file found" because uvicorn's cwd wasn't the repo
    # root, even though .secrets/credentials.json existed there.
    @field_validator("gmail_client_secrets_path", "gmail_token_path", "db_path", mode="after")
    @classmethod
    def _resolve_against_project_root(cls, value: Path) -> Path:
        if value.is_absolute():
            return value
        return PROJECT_ROOT / value


class PlatformSource(BaseModel):
    id: str
    label: str
    sender_domains: list[str]


class SourcesConfig(BaseModel):
    confirmation_keywords: list[str]
    # Specific multi-word phrases searched across the WHOLE email (not
    # subject-restricted like confirmation_keywords), to catch interview
    # invitations whose subjects carry no application keyword ("Meeting invite",
    # "Invitation to a first conversation"). Kept specific so the broader
    # full-text search doesn't flood; scrutinize_relevance filters the rest.
    # Defaulted so existing configs/tests without the key still load.
    invitation_phrases: list[str] = []
    platforms: list[PlatformSource]


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_sources(path: Path | None = None) -> SourcesConfig:
    sources_path = path or (PROJECT_ROOT / "backend" / "config" / "sources.yaml")
    raw = yaml.safe_load(sources_path.read_text())
    return SourcesConfig.model_validate(raw)
