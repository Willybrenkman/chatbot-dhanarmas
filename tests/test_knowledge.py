"""Pemuatan korpus dan pengaman dokumen contoh."""

from pathlib import Path

import pytest

from app.knowledge import load_corpus, periksa_korpus

KNOWLEDGE = Path(__file__).resolve().parent.parent / "knowledge"


@pytest.fixture(scope="module")
def corpus():
    return load_corpus(KNOWLEDGE)


def test_semua_dokumen_terbaca(corpus):
    assert len(corpus.documents) >= 6


def test_front_matter_terurai(corpus):
    d = next(x for x in corpus.documents if x.filename.startswith("20-"))
    assert d.judul
    assert d.berlaku_sejak == "2026-01-01"
    assert d.pemilik != "tidak dicantumkan"


def test_dokumen_contoh_ditandai(corpus):
    assert corpus.sample_docs, "dokumen contoh harus terdeteksi"
    assert len(corpus.sample_docs) == len(corpus.documents)


def test_blok_prompt_memuat_penanda_dokumen(corpus):
    blok = corpus.as_prompt_block()
    for d in corpus.documents:
        assert f"DOKUMEN: {d.filename}" in blok
    assert "DOKUMEN CONTOH" in blok, "penanda contoh harus terlihat model"


def test_mode_draft_tidak_diblokir_dokumen_contoh(corpus):
    masalah = periksa_korpus(corpus, answer_mode="draft", allow_sample=False)
    assert not [m for m in masalah if m.startswith("Mode 'auto' ditolak")]


def test_mode_auto_ditolak_kalau_masih_dokumen_contoh(corpus):
    masalah = periksa_korpus(corpus, answer_mode="auto", allow_sample=False)
    assert any(m.startswith("Mode 'auto' ditolak") for m in masalah)


def test_mode_auto_boleh_kalau_sengaja_diizinkan(corpus):
    masalah = periksa_korpus(corpus, answer_mode="auto", allow_sample=True)
    assert not [m for m in masalah if m.startswith("Mode 'auto' ditolak")]


def test_korpus_kosong_dilaporkan(tmp_path):
    masalah = periksa_korpus(
        load_corpus(tmp_path), answer_mode="draft", allow_sample=False
    )
    assert masalah and "Tidak ada dokumen" in masalah[0]


def test_dokumen_tanpa_tanggal_berlaku_diperingatkan(tmp_path):
    (tmp_path / "x.md").write_text("## Pasal 1\nIsi apa saja.", encoding="utf-8")
    masalah = periksa_korpus(
        load_corpus(tmp_path), answer_mode="draft", allow_sample=False
    )
    assert any("berlaku_sejak" in m for m in masalah)
