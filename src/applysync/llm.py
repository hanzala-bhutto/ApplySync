from __future__ import annotations

from langchain_nvidia_ai_endpoints import ChatNVIDIA

from applysync.config import Settings


def get_chat_model(settings: Settings) -> ChatNVIDIA:
    return ChatNVIDIA(model=settings.llm_model, api_key=settings.nvidia_api_key)
