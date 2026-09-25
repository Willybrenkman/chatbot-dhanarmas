"""Pemilihan penyedia LLM berdasarkan konfigurasi."""

from __future__ import annotations

from ..config import Settings
from .base import KodeGalat, LLMProvider, LLMResponse, ProviderError

__all__ = [
    "KodeGalat",
    "LLMProvider",
    "LLMResponse",
    "ProviderError",
    "build_provider",
]


def build_provider(settings: Settings) -> LLMProvider:
    nama = settings.llm_provider

    if nama == "anthropic":
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
            effort=settings.anthropic_effort,
        )

    if nama in ("groq", "hermes"):
        from .openai_compatible import PRESET, OpenAICompatibleProvider

        preset = PRESET[nama]
        if nama == "groq":
            base_url = settings.groq_base_url or preset["base_url"]
            model = settings.groq_model or preset["model"]
            api_key = settings.groq_api_key or ""
            if not api_key:
                raise RuntimeError(
                    "LLM_PROVIDER=groq butuh GROQ_API_KEY. Ambil kunci gratis di "
                    "https://console.groq.com/keys lalu taruh di .env."
                )
        else:
            base_url = settings.hermes_base_url or preset["base_url"]
            model = settings.hermes_model or preset["model"]
            api_key = settings.hermes_api_key

        return OpenAICompatibleProvider(
            base_url=base_url, model=model, api_key=api_key, name=nama
        )

    from .mock_provider import MockProvider

    return MockProvider()
