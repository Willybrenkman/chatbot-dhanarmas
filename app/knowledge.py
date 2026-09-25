"""Pemuat korpus dokumen kebijakan HRD.

Pendekatan MVP: seluruh dokumen dimasukkan ke context, TANPA RAG.
Alasannya (lihat README bagian "Kenapa tanpa RAG"):
  - korpus kebijakan HR umumnya < 300 halaman, masih jauh di bawah context window
  - tidak ada bug "potongan relevan tidak terambil" yang merusak akurasi
  - hemat 1-2 minggu kerja pada fase pilot

Korpus ditaruh di blok `system` dan di-cache (prompt caching), sehingga
dibaca ulang tiap turn dengan biaya ~10%.

Pindah ke RAG nanti cukup mengganti fungsi build_corpus() — pemanggilnya
tidak perlu berubah.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)

# Bawaan untuk model ber-context besar. Pemanggil sebaiknya mengoper ambang
# yang sesuai context window model yang dipakai (lihat Settings.ambang_token_korpus).
AMBANG_PERINGATAN_TOKEN = 400_000
# Perkiraan kasar token untuk Bahasa Indonesia (~3.5 karakter per token).
KARAKTER_PER_TOKEN = 3.5


@dataclass
class Document:
    filename: str
    judul: str
    berlaku_sejak: str
    pemilik: str
    versi: str
    contoh: bool
    body: str

    @property
    def perkiraan_token(self) -> int:
        return int(len(self.body) / KARAKTER_PER_TOKEN)


@dataclass
class Corpus:
    documents: list[Document] = field(default_factory=list)

    @property
    def filenames(self) -> list[str]:
        return [d.filename for d in self.documents]

    @property
    def sample_docs(self) -> list[str]:
        return [d.filename for d in self.documents if d.contoh]

    @property
    def perkiraan_token(self) -> int:
        return sum(d.perkiraan_token for d in self.documents)

    def as_prompt_block(self) -> str:
        """Render korpus jadi satu blok teks yang bisa disitasi model."""
        if not self.documents:
            return "(TIDAK ADA DOKUMEN KEBIJAKAN YANG DIMUAT)"
        bagian = []
        for d in self.documents:
            tanda = " [DOKUMEN CONTOH — BUKAN ATURAN RESMI]" if d.contoh else ""
            bagian.append(
                f"===== DOKUMEN: {d.filename}{tanda} =====\n"
                f"Judul        : {d.judul}\n"
                f"Berlaku sejak: {d.berlaku_sejak}\n"
                f"Pemilik      : {d.pemilik}\n"
                f"Versi        : {d.versi}\n\n"
                f"{d.body.strip()}\n"
            )
        return "\n".join(bagian)

    def daftar_dokumen(self) -> str:
        return "\n".join(
            f"- {d.filename} — {d.judul} (berlaku sejak {d.berlaku_sejak})"
            for d in self.documents
        )


def _parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    m = FRONT_MATTER_RE.match(text)
    if not m:
        return {}, text
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip().lower()] = v.strip().strip('"').strip("'")
    return meta, text[m.end():]


def load_corpus(knowledge_dir: Path) -> Corpus:
    """Muat semua .md di knowledge_dir, urut nama file (prefiks angka mengatur urutan)."""
    corpus = Corpus()
    if not knowledge_dir.exists():
        return corpus
    for path in sorted(knowledge_dir.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        meta, body = _parse_front_matter(raw)
        corpus.documents.append(
            Document(
                filename=path.name,
                judul=meta.get("judul", path.stem),
                berlaku_sejak=meta.get("berlaku_sejak", "tidak dicantumkan"),
                pemilik=meta.get("pemilik", "tidak dicantumkan"),
                versi=meta.get("versi", "-"),
                contoh=meta.get("contoh", "false").lower() in {"true", "ya", "1"},
                body=body,
            )
        )
    return corpus


def periksa_korpus(
    corpus: Corpus,
    *,
    answer_mode: str,
    allow_sample: bool,
    ambang_token: int = AMBANG_PERINGATAN_TOKEN,
) -> list[str]:
    """Kembalikan daftar masalah. Kosong = korpus layak dipakai.

    Pengaman utama: dokumen contoh TIDAK BOLEH dipakai di mode auto,
    supaya data demo tidak pernah terkirim ke karyawan sebagai aturan resmi.
    """
    masalah: list[str] = []
    if not corpus.documents:
        masalah.append(
            f"Tidak ada dokumen .md yang dimuat. Taruh dokumen kebijakan di folder knowledge/."
        )
        return masalah

    if corpus.sample_docs and answer_mode == "auto" and not allow_sample:
        masalah.append(
            "Mode 'auto' ditolak karena korpus masih berisi dokumen CONTOH: "
            + ", ".join(corpus.sample_docs)
            + ". Ganti dengan dokumen resmi HRD (hapus 'contoh: true' di front matter), "
            "atau set ALLOW_SAMPLE_DOCS_IN_AUTO=true kalau ini memang demo."
        )

    tanpa_tanggal = [d.filename for d in corpus.documents if d.berlaku_sejak == "tidak dicantumkan"]
    if tanpa_tanggal:
        masalah.append(
            "Dokumen tanpa 'berlaku_sejak' (berisiko menyebarkan aturan usang): "
            + ", ".join(tanpa_tanggal)
        )

    if corpus.perkiraan_token > ambang_token:
        masalah.append(
            f"Korpus ~{corpus.perkiraan_token:,} token, melewati ambang "
            f"{ambang_token:,}. Ambang ini harus sesuai context window model yang "
            "dipakai; kalau sudah terlampaui, saatnya pindah dari full-context ke "
            "RAG (lihat README bagian 'Kapan pindah ke RAG')."
        )
    return masalah
