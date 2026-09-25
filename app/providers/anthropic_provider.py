"""Penyedia Claude API — rekomendasi untuk fase pilot.

Dua hal penting di sini:

1. PROMPT CACHING. Korpus kebijakan ditaruh di blok `system` pertama dengan
   cache_control ttl 1 jam. Blok volatil menyusul SETELAHNYA supaya tidak
   membatalkan cache. Dengan lalu lintas yang jalan sepanjang hari kerja,
   cache tetap hangat dan pembacaan korpus jadi ~10% harga.
   Verifikasi lewat field cached_tokens di log — kalau nol terus, ada yang
   membatalkan cache (biasanya ada timestamp atau ID yang ikut di prefiks).

2. EFFORT RENDAH. Tanya-jawab kebijakan itu beban chat, bukan penalaran
   panjang. Effort 'low' dengan adaptive thinking memberi akurasi yang cukup
   dengan biaya dan latensi jauh lebih rendah. Naikkan hanya kalau eval
   menunjukkan ada ruang perbaikan.
"""

from __future__ import annotations

import logging
import time

from anthropic import AsyncAnthropic

from .base import LLMResponse

log = logging.getLogger(__name__)

PESAN_DITOLAK = (
    "Maaf, aku tidak bisa memproses pertanyaan ini. Pertanyaanmu sudah aku "
    "teruskan ke HRD supaya ditangani langsung oleh manusia."
)


class AnthropicProvider:
    name = "anthropic"
    supports_cache = True

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str = "claude-opus-5",
        effort: str = "low",
    ) -> None:
        # Tanpa api_key eksplisit, SDK membaca ANTHROPIC_API_KEY atau profil
        # kredensial yang aktif di mesin ini.
        self._client = AsyncAnthropic(api_key=api_key) if api_key else AsyncAnthropic()
        self.model = model
        self.effort = effort

    async def complete(
        self,
        *,
        stable_system: str,
        volatile_system: str,
        messages: list[dict[str, str]],
        max_output_tokens: int,
    ) -> LLMResponse:
        mulai = time.perf_counter()

        system_blocks = [
            {
                "type": "text",
                "text": stable_system,
                # Korpus besar dan tidak berubah -> cache selama 1 jam.
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            },
            # Blok volatil HARUS setelah blok yang di-cache.
            {"type": "text", "text": volatile_system},
        ]

        # Streaming dipakai karena input-nya besar (korpus penuh); ini
        # menghindari timeout HTTP pada permintaan non-streaming.
        async with self._client.messages.stream(
            model=self.model,
            max_tokens=max_output_tokens,
            system=system_blocks,
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
        ) as stream:
            msg = await stream.get_final_message()

        latency = int((time.perf_counter() - mulai) * 1000)
        usage = msg.usage

        # Klasifikasi keamanan bisa menolak permintaan (HTTP 200, stop_reason
        # 'refusal'). Selalu periksa sebelum membaca isi.
        if msg.stop_reason == "refusal":
            detail = getattr(msg, "stop_details", None)
            log.warning(
                "Permintaan ditolak oleh model. kategori=%s",
                getattr(detail, "category", None),
            )
            teks = PESAN_DITOLAK
        else:
            teks = "\n".join(
                b.text for b in msg.content if getattr(b, "type", None) == "text"
            ).strip()
            if not teks:
                log.warning("Respons tanpa blok teks. stop_reason=%s", msg.stop_reason)
                teks = PESAN_DITOLAK

        return LLMResponse(
            text=teks,
            provider=self.name,
            model=msg.model or self.model,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cached_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            latency_ms=latency,
            supports_cache=True,
        )
