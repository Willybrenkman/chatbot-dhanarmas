"""Pemilihan penyedia LLM berdasarkan konfigurasi."""

from __future__ import annotations

from ..config import Settings
from .base import LLMProvider, LLMResponse

__all__ = ["LLMProvider", "LLMResponse", "build_provider"]


def build_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "anthropic":
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
            effort=settings.anthropic_effort,
        )
    if settings.llm_provider == "hermes":
        from .hermes_provider import HermesProvider

        return HermesProvider(
            base_url=settings.hermes_base_url,
            model=settings.hermes_model,
            api_key=settings.hermes_api_key,
        )
    from .mock_provider import MockProvider

    return MockProvider()
