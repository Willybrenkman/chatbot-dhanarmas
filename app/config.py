"""Konfigurasi aplikasi, dibaca dari environment / file .env."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Identitas ---
    nama_perusahaan: str = "PT Dhanarmas"
    nama_bot: str = "Asisten HRD"

    # --- Mode jawaban ---
    # draft = jawaban ditahan, HRD meninjau lalu mengirim (mulai dari sini)
    # auto  = jawaban langsung ke karyawan
    answer_mode: Literal["draft", "auto"] = "draft"

    # --- Penyedia LLM ---
    # mock      = tanpa panggilan jaringan, untuk tes & demo offline
    # anthropic = Claude API
    # groq      = Groq (model terbuka, ada tier gratis) lewat OpenAI-compatible
    # hermes    = endpoint OpenAI-compatible milik sendiri (vLLM), untuk on-prem
    llm_provider: Literal["mock", "anthropic", "groq", "hermes"] = "mock"

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5"
    # Chat tanya-jawab tidak butuh penalaran dalam; effort rendah lebih murah & cepat.
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"

    # Groq — kunci gratis di https://console.groq.com/keys
    # Nama model Groq berubah dan yang lama dihentikan; verifikasi dengan
    # `python3 scripts/cek_provider.py --daftar-model` sebelum menetapkannya.
    groq_api_key: str | None = None
    groq_model: str = "llama-3.3-70b-versatile"
    groq_base_url: str = "https://api.groq.com/openai/v1"

    hermes_base_url: str = "http://localhost:8000/v1"
    hermes_model: str = "NousResearch/Hermes-3-Llama-3.1-8B"
    hermes_api_key: str = "not-needed"

    max_output_tokens: int = 1500
    # Berapa turn terakhir yang dibawa sebagai riwayat percakapan.
    history_turns: int = 6

    # --- Basis data ---
    # SQLite cukup untuk pilot. Skemanya sengaja dibuat agar pindah ke
    # Postgres nanti hanya mengganti driver, bukan menulis ulang.
    database_path: Path = BASE_DIR / "data" / "hrd_chatbot.sqlite3"

    # --- Dokumen kebijakan ---
    knowledge_dir: Path = BASE_DIR / "knowledge"
    # Ambang peringatan ukuran korpus, dalam token perkiraan. Harus disesuaikan
    # dengan context window model yang dipakai, dengan sisa ruang untuk riwayat
    # percakapan dan jawaban:
    #   Claude (1 juta token)            -> 400_000 aman
    #   Llama 3.3 70B di Groq (128rb)    -> 60_000
    #   model 8B (8rb-32rb)              -> 6_000
    # Melewati ambang ini berarti saatnya pindah dari full-context ke RAG.
    ambang_token_korpus: int = 400_000
    # Pengaman: menolak jalan di mode auto kalau korpus masih berisi dokumen contoh.
    allow_sample_docs_in_auto: bool = False

    # --- Konsol HRD ---
    # MVP: satu token bersama. WAJIB diganti SSO sebelum produksi.
    hrd_console_token: str = "ganti-token-ini"

    # --- Kontak eskalasi (diisi HRD) ---
    kontak_hrd: str = "HRD — ext. 100 / hrd@contoh.co.id"
    kontak_krisis: str = "Layanan SEJIWA Kemenkes: 119 ext. 8 (mohon HRD verifikasi & sesuaikan)"

    @property
    def disclaimer(self) -> str:
        return (
            f"Jawaban ini disusun otomatis dari dokumen resmi {self.nama_perusahaan} "
            "dan bukan keputusan resmi HRD. Untuk hal yang mengikat, "
            f"mohon konfirmasi ke {self.kontak_hrd}."
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
