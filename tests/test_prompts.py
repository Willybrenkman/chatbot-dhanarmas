"""Pemisahan blok stabil (di-cache) dan volatil."""

from app.prompts import SENTINEL_TIDAK_DITEMUKAN, build_stable_block, build_volatile_block

ARG = dict(
    nama_bot="Asisten HRD",
    nama_perusahaan="PT Dhanarmas",
    korpus="===== DOKUMEN: a.md =====\nisi",
    daftar_dokumen="- a.md — A",
)


def test_blok_stabil_identik_untuk_semua_status():
    """Kalau ini gagal, korpus akan di-cache ulang untuk setiap status kerja."""
    assert build_stable_block(**ARG) == build_stable_block(**ARG)


def test_blok_stabil_tidak_memuat_konteks_sesi():
    blok = build_stable_block(**ARG)
    for status in ("tetap", "pkwt", "harian", "outsourcing", "magang"):
        assert build_volatile_block(status) not in blok


def test_blok_stabil_memuat_aturan_wajib():
    blok = build_stable_block(**ARG)
    assert SENTINEL_TIDAK_DITEMUKAN in blok
    assert "[sumber:" in blok
    assert "korpus" not in blok.lower().split("dokumen kebijakan")[0][:50]


def test_blok_volatil_berbeda_per_status():
    assert build_volatile_block("tetap") != build_volatile_block("pkwt")
    assert "tidak diketahui" in build_volatile_block("entah_apa").lower()
