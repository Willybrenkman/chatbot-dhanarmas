"""Kontrak penyedia LLM.

Aplikasi hanya bicara ke antarmuka ini, jadi berpindah antara Claude API,
Groq, dan model self-hosted cukup mengganti satu variabel environment.
Ini juga yang memungkinkan pola hibrida: API terkelola untuk tanya-jawab
kebijakan (tanpa PII), model on-prem untuk apa pun yang menyentuh data
pribadi karyawan.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

KodeGalat = Literal["rate_limited", "auth", "konteks_penuh", "model_tidak_ada", "lain"]


class ProviderError(Exception):
    """Kegagalan penyedia yang sudah diklasifikasi.

    Kodenya dipakai app/chat.py untuk memberi pesan yang tepat ke karyawan.
    Tanpa ini semuanya jadi "gangguan teknis", padahal "sedang ramai, coba
    lagi sebentar" dan "kunci API salah" butuh penanganan yang beda —
    terutama di tier gratis yang kena batas laju.
    """

    def __init__(self, kode: KodeGalat, pesan: str, *, retry_after_s: float | None = None) -> None:
        super().__init__(pesan)
        self.kode: KodeGalat = kode
        self.retry_after_s = retry_after_s


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    latency_ms: int = 0
    # False untuk penyedia yang tidak punya prompt caching (Groq, vLLM tanpa
    # prefix caching). Dipakai supaya peringatan "cache_read selalu nol"
    # tidak muncul untuk penyedia yang memang tidak mendukungnya.
    supports_cache: bool = True


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    model: str
    supports_cache: bool

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

        Kegagalan dilempar sebagai ProviderError dengan kode yang sesuai.
        """
        ...
