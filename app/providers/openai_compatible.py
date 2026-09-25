"""Penyedia untuk endpoint OpenAI-compatible.

Satu kelas melayani beberapa tujuan, karena bentuk API-nya sama:

  - Groq              https://api.groq.com/openai/v1   (model terbuka, ada tier gratis)
  - vLLM / Ollama     http://server-sendiri:8000/v1    (on-prem, data tidak keluar)
  - endpoint lain yang mengikuti bentuk /chat/completions

Perbedaannya hanya base_url, nama model, dan kunci API — jadi semuanya
dikonfigurasi lewat environment, bukan lewat kelas terpisah.

Dua hal yang perlu diperhatikan dibanding Claude API:

1. TIDAK ADA PROMPT CACHING eksplisit. Format OpenAI tidak punya
   cache_control per blok, jadi korpus kebijakan dibaca ulang seharga penuh
   setiap turn. vLLM melakukan automatic prefix caching sehingga menaruh
   korpus di awal tetap menguntungkan; Groq tidak melaporkan cache hit.
   Karena itu supports_cache = False dan metrik cache_read tidak dianggap
   sebagai tanda ada yang rusak.

2. KEPATUHAN INSTRUKSI LEBIH RAPUH. Model terbuka lebih sering melewatkan
   format sitasi atau sentinel TIDAK_DITEMUKAN. Itu justru alasan pemeriksaan
   sitasi dan sentinel di app/chat.py ada: jawaban yang tidak patuh ditahan
   untuk ditinjau manusia, bukan diteruskan ke karyawan.
"""

from __future__ import annotations

import logging
import time

import httpx

from .base import LLMResponse, ProviderError

log = logging.getLogger(__name__)

# Preset supaya pengguna cukup menaruh kunci API.
PRESET = {
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        # Nama model Groq berubah dan yang lama dihentikan. Verifikasi dengan
        # `python3 scripts/cek_provider.py --daftar-model` sebelum dipakai.
        "model": "llama-3.3-70b-versatile",
    },
    "hermes": {
        "base_url": "http://localhost:8000/v1",
        "model": "NousResearch/Hermes-3-Llama-3.1-8B",
    },
}


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str = "not-needed",
        name: str = "openai_compatible",
        timeout_s: float = 120.0,
        temperature: float = 0.2,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.name = name
        self.supports_cache = False
        self._api_key = api_key
        self._timeout = timeout_s
        self._temperature = temperature

    def _klasifikasi(self, resp: httpx.Response) -> ProviderError:
        """Ubah galat HTTP jadi kode yang bisa ditindaklanjuti."""
        cuplikan = resp.text[:300]
        if resp.status_code == 429:
            ra = resp.headers.get("retry-after")
            try:
                jeda = float(ra) if ra else None
            except ValueError:
                jeda = None
            return ProviderError(
                "rate_limited",
                f"Batas laju penyedia tercapai: {cuplikan}",
                retry_after_s=jeda,
            )
        if resp.status_code in (401, 403):
            return ProviderError("auth", f"Kunci API ditolak: {cuplikan}")
        if resp.status_code == 404:
            return ProviderError(
                "model_tidak_ada",
                f"Model '{self.model}' tidak ditemukan di {self.base_url}. "
                f"Jalankan scripts/cek_provider.py --daftar-model. Detail: {cuplikan}",
            )
        if resp.status_code == 400 and any(
            k in cuplikan.lower()
            for k in ("context", "token", "too long", "maximum", "reduce")
        ):
            return ProviderError(
                "konteks_penuh",
                "Prompt melebihi context window model. Korpus kebijakan terlalu "
                f"besar untuk model ini — pakai model dengan context lebih besar, "
                f"atau pindah ke RAG. Detail: {cuplikan}",
            )
        return ProviderError("lain", f"HTTP {resp.status_code}: {cuplikan}")

    async def complete(
        self,
        *,
        stable_system: str,
        volatile_system: str,
        messages: list[dict[str, str]],
        max_output_tokens: int,
    ) -> LLMResponse:
        mulai = time.perf_counter()

        # Urutan dijaga: blok stabil dulu, volatil sesudahnya. Format OpenAI
        # tidak punya cache_control per blok, tapi urutan ini tetap penting
        # untuk automatic prefix caching di vLLM.
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": f"{stable_system}\n\n{volatile_system}"},
                *messages,
            ],
            "max_tokens": max_output_tokens,
            # Rendah supaya jawaban kebijakan konsisten, bukan kreatif.
            "temperature": self._temperature,
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
        except httpx.TimeoutException as exc:
            raise ProviderError("lain", f"Penyedia tidak merespons dalam {self._timeout}s") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("lain", f"Gagal menghubungi {self.base_url}: {exc}") from exc

        if resp.status_code >= 400:
            galat = self._klasifikasi(resp)
            log.error("%s gagal (%s): %s", self.name, galat.kode, galat)
            raise galat

        data = resp.json()
        pilihan = (data.get("choices") or [{}])[0]
        teks = ((pilihan.get("message") or {}).get("content") or "").strip()

        if not teks:
            raise ProviderError(
                "lain",
                f"Penyedia membalas tanpa teks (finish_reason={pilihan.get('finish_reason')})",
            )

        usage = data.get("usage") or {}
        return LLMResponse(
            text=teks,
            provider=self.name,
            model=data.get("model", self.model),
            input_tokens=usage.get("prompt_tokens", 0) or 0,
            output_tokens=usage.get("completion_tokens", 0) or 0,
            # Dilaporkan vLLM bila prefix caching aktif; Groq tidak mengisinya.
            cached_tokens=(usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0,
            latency_ms=int((time.perf_counter() - mulai) * 1000),
            supports_cache=False,
        )
