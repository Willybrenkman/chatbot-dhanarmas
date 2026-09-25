"""Kontrak penyedia LLM.

Aplikasi hanya bicara ke antarmuka ini, jadi pindah dari Claude API ke
Hermes on-prem (atau sebaliknya) cukup mengganti satu variabel environment.
Ini juga yang memungkinkan pola hibrida yang direkomendasikan: API terkelola
untuk tanya-jawab kebijakan (tanpa PII), Hermes on-prem untuk apa pun yang
menyentuh data pribadi karyawan.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    latency_ms: int = 0


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    model: str

    async def complete(
        self,
        *,
        stable_system: str,
        volatile_system: str,
        messages: list[dict[str, str]],
        max_output_tokens: int,
    ) -> LLMResponse:
        """Hasilkan jawaban.

        stable_system   : bagian prompt yang identik antar sesi (layak di-cache)
        volatile_system : konteks per-sesi, dirender setelah blok stabil
        messages        : riwayat [{"role": "user"|"assistant", "content": str}]
        """
        ...
