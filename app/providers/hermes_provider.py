"""Penyedia Hermes self-hosted lewat endpoint OpenAI-compatible (vLLM).

Ini jalur untuk saat kebijakan data mengharuskan inferensi tetap di dalam
infrastruktur sendiri. Endpoint-nya milik kalian, bukan layanan pihak ketiga
— jadi ini HTTP biasa ke server sendiri, bukan shim ke penyedia lain.

Catatan kapasitas (lihat README bagian biaya): satu GPU kelas L4 cukup untuk
model 8B pada beban normal, tetapi beban puncak (THR, cuti bersama) bisa 3-5x
dan butuh GPU kedua. Model 70B — yang jauh lebih andal untuk mengikuti
instruksi sitasi — butuh A100/H100.

vLLM melakukan automatic prefix caching, jadi menaruh korpus di awal prompt
tetap menguntungkan seperti pada Claude API.
"""

from __future__ import annotations

import logging
import time

import httpx

from .base import LLMResponse

log = logging.getLogger(__name__)


class HermesProvider:
    name = "hermes"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str = "not-needed",
        timeout_s: float = 120.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        self._timeout = timeout_s

    async def complete(
        self,
        *,
        stable_system: str,
        volatile_system: str,
        messages: list[dict[str, str]],
        max_output_tokens: int,
    ) -> LLMResponse:
        mulai = time.perf_counter()

        # Format OpenAI tidak punya cache_control per blok, jadi kedua bagian
        # digabung. Urutannya tetap dijaga (stabil dulu) supaya automatic
        # prefix caching vLLM bisa bekerja.
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": f"{stable_system}\n\n{volatile_system}"},
                *messages,
            ],
            "max_tokens": max_output_tokens,
            # Rendah supaya jawaban kebijakan konsisten, bukan kreatif.
            "temperature": 0.2,
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()

        pilihan = (data.get("choices") or [{}])[0]
        teks = (pilihan.get("message") or {}).get("content", "").strip()
        if not teks:
            log.warning("Hermes membalas tanpa teks. finish_reason=%s",
                        pilihan.get("finish_reason"))
            teks = (
                "Maaf, aku tidak bisa menyusun jawaban saat ini. Pertanyaanmu "
                "sudah aku teruskan ke HRD."
            )

        usage = data.get("usage") or {}
        return LLMResponse(
            text=teks,
            provider=self.name,
            model=data.get("model", self.model),
            input_tokens=usage.get("prompt_tokens", 0) or 0,
            output_tokens=usage.get("completion_tokens", 0) or 0,
            # vLLM melaporkan prefix cache hit di bawah kunci ini bila tersedia.
            cached_tokens=(usage.get("prompt_tokens_details") or {}).get(
                "cached_tokens", 0
            ) or 0,
            latency_ms=int((time.perf_counter() - mulai) * 1000),
        )
