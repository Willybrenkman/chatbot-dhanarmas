#!/usr/bin/env python3
"""Periksa penyedia LLM yang dikonfigurasi: kunci API, nama model, dan satu jawaban nyata.

Jalankan ini SEBELUM menyalakan aplikasi, supaya masalah kunci atau nama model
ketahuan di sini, bukan saat karyawan sudah bertanya.

    python3 scripts/cek_provider.py --daftar-model    # nama model yang tersedia
    python3 scripts/cek_provider.py                   # satu pertanyaan uji
    python3 scripts/cek_provider.py --tanya "Cuti tahunan berapa hari?"
    python3 scripts/cek_provider.py --ukur-korpus     # cek korpus vs context model

Nama model Groq berubah dan yang lama dihentikan tanpa banyak pemberitahuan.
Karena itu --daftar-model membaca daftarnya dari API, bukan dari daftar yang
ditulis di kode — yang pasti akan basi.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.knowledge import load_corpus  # noqa: E402
from app.prompts import build_stable_block, build_volatile_block  # noqa: E402
from app.providers import ProviderError, build_provider  # noqa: E402

C = {"ok": "\033[32m", "no": "\033[31m", "warn": "\033[33m", "dim": "\033[2m", "off": "\033[0m"}


def w(teks: str, kode: str) -> str:
    return f"{C[kode]}{teks}{C['off']}" if sys.stdout.isatty() else teks


def base_url_dan_kunci(s) -> tuple[str | None, str | None]:
    if s.llm_provider == "groq":
        return s.groq_base_url, s.groq_api_key
    if s.llm_provider == "hermes":
        return s.hermes_base_url, s.hermes_api_key
    return None, None


def daftar_model(s) -> int:
    base, kunci = base_url_dan_kunci(s)
    if not base:
        print(w(f"--daftar-model hanya untuk penyedia OpenAI-compatible "
                f"(groq/hermes). Sekarang: {s.llm_provider}", "warn"))
        return 1
    if s.llm_provider == "groq" and not kunci:
        print(w("GROQ_API_KEY belum diisi di .env. Ambil kunci gratis di "
                "https://console.groq.com/keys", "no"))
        return 1

    try:
        r = httpx.get(
            f"{base.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {kunci or 'not-needed'}"},
            timeout=30,
        )
    except httpx.HTTPError as exc:
        print(w(f"Tidak bisa menghubungi {base}: {exc}", "no"))
        return 1

    if r.status_code in (401, 403):
        print(w("Kunci API ditolak. Periksa GROQ_API_KEY di .env.", "no"))
        return 1
    if r.status_code >= 400:
        print(w(f"HTTP {r.status_code}: {r.text[:200]}", "no"))
        return 1

    model = sorted(
        (m for m in (r.json().get("data") or [])),
        key=lambda m: str(m.get("id", "")),
    )
    print(f"{len(model)} model tersedia di {base}\n")
    print(f"{'id':44s} {'context':>9s}  {'maks output':>11s}")
    print("-" * 70)
    for m in model:
        ctx = m.get("context_window") or m.get("max_context_length") or "-"
        out = m.get("max_completion_tokens") or "-"
        aktif = m.get("active")
        tanda = "" if aktif in (None, True) else w("  (tidak aktif)", "dim")
        ctx_s = f"{ctx:,}" if isinstance(ctx, int) else str(ctx)
        out_s = f"{out:,}" if isinstance(out, int) else str(out)
        print(f"{str(m.get('id'))[:43]:44s} {ctx_s:>9s}  {out_s:>11s}{tanda}")

    print(w("\nSalin salah satu id ke GROQ_MODEL di .env.", "dim"))
    print(w("Pilih yang context window-nya jauh lebih besar dari korpus kebijakanmu "
            "(cek dengan --ukur-korpus).", "dim"))
    return 0


def ukur_korpus(s) -> int:
    corpus = load_corpus(s.knowledge_dir)
    stabil = build_stable_block(
        nama_bot=s.nama_bot, nama_perusahaan=s.nama_perusahaan,
        korpus=corpus.as_prompt_block(), daftar_dokumen=corpus.daftar_dokumen(),
    )
    volatil = build_volatile_block("tetap")
    # Perkiraan kasar, ~3,5 karakter per token untuk Bahasa Indonesia.
    perkiraan = int((len(stabil) + len(volatil)) / 3.5)
    print(f"dokumen            : {len(corpus.documents)}")
    print(f"korpus saja        : ~{corpus.perkiraan_token:,} token")
    print(f"prompt penuh       : ~{perkiraan:,} token (instruksi + korpus + konteks sesi)")
    print(f"ambang peringatan  : {s.ambang_token_korpus:,} token")
    print()
    print("Prompt penuh dikirim setiap turn, jadi context window model harus")
    print("cukup untuk itu ditambah riwayat percakapan dan jawaban. Patokan aman:")
    print(f"  context model minimal ~{int(perkiraan * 1.6):,} token")
    print()
    if perkiraan > s.ambang_token_korpus:
        print(w("Prompt melewati ambang. Naikkan AMBANG_TOKEN_KORPUS kalau model "
                "kamu memang besar, atau pindah ke RAG.", "warn"))
    else:
        print(w("Ukuran prompt masih di bawah ambang.", "ok"))
    return 0


async def tanya_uji(s, pertanyaan: str) -> int:
    corpus = load_corpus(s.knowledge_dir)
    if not corpus.documents:
        print(w("Tidak ada dokumen di knowledge/. Isi dulu sebelum menguji.", "no"))
        return 1

    try:
        provider = build_provider(s)
    except RuntimeError as exc:
        print(w(str(exc), "no"))
        return 1

    print(f"penyedia : {provider.name}")
    print(f"model    : {provider.model}")
    print(f"dokumen  : {len(corpus.documents)} (~{corpus.perkiraan_token:,} token)")
    print(f"pertanyaan: {pertanyaan}")
    print("-" * 70)

    stabil = build_stable_block(
        nama_bot=s.nama_bot, nama_perusahaan=s.nama_perusahaan,
        korpus=corpus.as_prompt_block(), daftar_dokumen=corpus.daftar_dokumen(),
    )

    try:
        resp = await provider.complete(
            stable_system=stabil,
            volatile_system=build_volatile_block("tetap"),
            messages=[{"role": "user", "content": pertanyaan}],
            max_output_tokens=s.max_output_tokens,
        )
    except ProviderError as galat:
        print(w(f"GAGAL [{galat.kode}] {galat}", "no"))
        if galat.kode == "auth":
            print(w("Periksa kunci API di .env.", "dim"))
        elif galat.kode == "model_tidak_ada":
            print(w("Jalankan --daftar-model untuk melihat nama model yang benar.", "dim"))
        elif galat.kode == "rate_limited":
            print(w("Tier gratis kena batas laju. Tunggu lalu coba lagi.", "dim"))
        elif galat.kode == "konteks_penuh":
            print(w("Jalankan --ukur-korpus, lalu pakai model dengan context lebih besar.", "dim"))
        return 1
    except Exception as exc:  # noqa: BLE001
        print(w(f"GAGAL (tak terklasifikasi) {type(exc).__name__}: {exc}", "no"))
        return 1

    print(resp.text)
    print("-" * 70)
    print(f"latensi  : {resp.latency_ms:,} ms")
    print(f"token    : input {resp.input_tokens:,} | output {resp.output_tokens:,}"
          + (f" | cache_read {resp.cached_tokens:,}" if resp.supports_cache else ""))
    if not resp.supports_cache:
        print(w("Penyedia ini tanpa prompt caching: korpus dibaca ulang seharga "
                "penuh setiap turn.", "dim"))

    # Pemeriksaan yang sama dengan yang dipakai pipa, supaya kepatuhan model terlihat.
    from app.chat import SITASI  # noqa: PLC0415
    from app.prompts import SENTINEL_TIDAK_DITEMUKAN  # noqa: PLC0415

    dikenal = set(corpus.filenames)
    sitasi = [m[0].strip() for m in SITASI.findall(resp.text)]
    palsu = [c for c in sitasi if c not in dikenal]

    print()
    if resp.text.upper().startswith(SENTINEL_TIDAK_DITEMUKAN):
        print(w("Model mengaku tidak menemukan jawabannya. Kalau pertanyaan ini "
                "sebenarnya ada di dokumen, berarti kepatuhannya perlu diperiksa.", "warn"))
    elif not sitasi:
        print(w("TIDAK ADA SITASI. Jawaban seperti ini akan ditahan untuk ditinjau "
                "manusia oleh pipa, tidak dikirim otomatis.", "warn"))
    elif palsu:
        print(w(f"SITASI PALSU: {', '.join(palsu)} tidak ada di korpus. Pipa akan "
                "menahan jawaban ini.", "no"))
    else:
        print(w(f"Sitasi sah: {', '.join(sorted(set(sitasi)))}", "ok"))

    print(w("\nUji kepatuhan menyeluruh: LLM_PROVIDER=" + provider.name
            + " python3 eval/run_eval.py", "dim"))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Periksa penyedia LLM yang dikonfigurasi")
    ap.add_argument("--daftar-model", action="store_true")
    ap.add_argument("--ukur-korpus", action="store_true")
    ap.add_argument("--tanya", default="Cuti tahunan dapat berapa hari?")
    args = ap.parse_args()

    s = get_settings()
    if args.daftar_model:
        return daftar_model(s)
    if args.ukur_korpus:
        return ukur_korpus(s)
    return asyncio.run(tanya_uji(s, args.tanya))


if __name__ == "__main__":
    raise SystemExit(main())
