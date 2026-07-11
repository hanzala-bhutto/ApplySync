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
    nvidia_api_key: str = ""
    gmail_client_secrets_path: Path = Path(".secrets/credentials.json")
    gmail_token_path: Path = Path(".secrets/token.json")
    db_path: Path = Path("applysync.db")
    sync_interval_minutes: int = 20
    # Base URL of the self-hosted SearXNG instance (see searxng/docker-compose.yml)
    # that powers the web-research features. Local, keyless, no external account.
    searxng_url: str = "http://localhost:8888"

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
    platforms: list[PlatformSource]


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_sources(path: Path | None = None) -> SourcesConfig:
    sources_path = path or (PROJECT_ROOT / "backend" / "config" / "sources.yaml")
    raw = yaml.safe_load(sources_path.read_text())
    return SourcesConfig.model_validate(raw)
