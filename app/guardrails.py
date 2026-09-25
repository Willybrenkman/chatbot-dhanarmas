"""Pagar pengaman: deteksi topik yang tidak boleh dijawab bot.

Prinsip: pemeriksaan ini DETERMINISTIK (kata kunci/regex), dijalankan
SEBELUM memanggil model. Keselamatan tidak dititipkan ke prompt.
Prompt tetap diberi aturan yang sama sebagai lapisan kedua.

Aturan praktis yang dipakai: kalau sebuah jawaban bisa dijadikan bukti
dalam perselisihan hubungan industrial, itu bukan pekerjaan bot.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class Topik:
    kode: str
    urgensi: str          # normal | tinggi | kritis
    pola: tuple[str, ...]
    balasan: str


# Diurutkan dari paling berat. Pemeriksaan berhenti di kecocokan pertama.
TOPIK_TERLARANG: tuple[Topik, ...] = (
    Topik(
        kode="krisis",
        urgensi="kritis",
        pola=(
            r"\bbunuh diri\b", r"\bmengakhiri hidup\b", r"\bself harm\b",
            r"\bmelukai diri\b", r"\btidak (?:mau|ingin) hidup\b", r"\bputus asa\b",
        ),
        balasan=(
            "Terima kasih sudah menyampaikan ini. Aku tidak bisa membantu untuk hal "
            "sepenting ini, dan kamu tidak perlu menghadapinya sendiri.\n\n"
            "Pesanmu sudah aku teruskan ke HRD sebagai prioritas tertinggi, dan "
            "seseorang akan menghubungimu.\n\n"
            "Kalau butuh bicara dengan seseorang sekarang: {kontak_krisis}"
        ),
    ),
    Topik(
        kode="pelecehan_kekerasan",
        urgensi="kritis",
        pola=(
            r"\bpelecehan\b", r"\bdilecehkan\b", r"\bharassment\b",
            r"\bkekerasan\b", r"\bdiancam\b", r"\bintimidasi\b", r"\bbully\b",
            r"\bperundungan\b",
        ),
        balasan=(
            "Ini hal serius dan harus ditangani langsung oleh manusia, bukan olehku.\n\n"
            "Laporanmu sudah aku teruskan ke HRD sebagai prioritas tinggi dan akan "
            "ditangani secara rahasia. Kalau kamu lebih nyaman menyampaikannya "
            "langsung, hubungi {kontak_hrd}."
        ),
    ),
    Topik(
        kode="phk_pesangon",
        urgensi="tinggi",
        pola=(
            r"\bphk\b", r"\bpesangon\b", r"\bdipecat\b", r"\bdiberhentikan\b",
            r"\bpemutusan hubungan kerja\b", r"\bdirumahkan\b",
        ),
        balasan=(
            "Untuk hal yang menyangkut pemutusan hubungan kerja dan hak-hak yang "
            "menyertainya, aku tidak boleh memberi jawaban — konsekuensinya terlalu "
            "besar untuk ditentukan otomatis.\n\n"
            "Pertanyaanmu sudah aku teruskan ke HRD. Silakan juga hubungi langsung "
            "{kontak_hrd}."
        ),
    ),
    Topik(
        kode="sanksi_disiplin",
        urgensi="tinggi",
        pola=(
            r"\bsp\s?[123]\b", r"\bsurat peringatan\b", r"\bsanksi\b",
            r"\bskorsing\b", r"\bteguran\b", r"\bdisiplin\b", r"\bpelanggaran saya\b",
        ),
        balasan=(
            "Hal yang menyangkut sanksi atau tindakan disiplin harus dijelaskan "
            "langsung oleh HRD, karena selalu bergantung pada konteks kasusnya.\n\n"
            "Pertanyaanmu sudah aku teruskan ke HRD. Kontak langsung: {kontak_hrd}."
        ),
    ),
    Topik(
        kode="sengketa_hukum",
        urgensi="tinggi",
        pola=(
            r"\bmenuntut\b", r"\bgugatan\b", r"\bpengadilan\b",
            r"\bhubungan industrial\b", r"\bdisnaker\b", r"\bperselisihan\b",
            r"\bpengacara\b", r"\bmediasi\b",
        ),
        balasan=(
            "Untuk hal yang sudah menyentuh ranah hukum, aku tidak bisa memberi "
            "penafsiran — itu bukan kewenanganku dan bisa merugikanmu kalau salah.\n\n"
            "Pertanyaanmu sudah aku teruskan ke HRD. Kontak langsung: {kontak_hrd}."
        ),
    ),
    Topik(
        kode="data_orang_lain",
        urgensi="normal",
        pola=(
            r"\bgaji\s+(?:teman|rekan|atasan|bos|si |pak |bu |mas |mbak )",
            r"\bberapa gaji\s+\w+", r"\bdata (?:karyawan|pegawai) lain\b",
            r"\bslip gaji\s+(?:teman|rekan|orang)",
        ),
        balasan=(
            "Aku tidak bisa memberikan data pribadi karyawan lain — termasuk gaji, "
            "tunjangan, atau data kepegawaian. Itu dilindungi dan hanya bisa diakses "
            "oleh yang bersangkutan.\n\n"
            "Kalau kamu butuh datamu sendiri, hubungi {kontak_hrd}."
        ),
    ),
    Topik(
        kode="konflik_personal",
        urgensi="normal",
        pola=(
            r"\bkonflik dengan\b", r"\bmasalah dengan (?:atasan|bos|rekan|tim)\b",
            r"\btidak cocok dengan (?:atasan|bos|rekan)\b",
            r"\batasan saya (?:tidak|selalu|sering)\b", r"\bdicurangi\b",
        ),
        balasan=(
            "Untuk hal yang menyangkut hubungan kerja dengan orang lain, aku tidak "
            "bisa membantu — ini butuh manusia yang bisa mendengar konteks lengkapnya.\n\n"
            "Pesanmu sudah aku teruskan ke HRD. Kontak langsung: {kontak_hrd}."
        ),
    ),
    Topik(
        kode="whistleblowing",
        urgensi="tinggi",
        pola=(
            r"\bkorupsi\b", r"\bfraud\b", r"\bpenggelapan\b", r"\bsuap\b",
            r"\bmelaporkan (?:kecurangan|pelanggaran)\b", r"\bwhistleblow",
        ),
        balasan=(
            "Laporan seperti ini harus lewat jalur resmi yang terjamin "
            "kerahasiaannya, bukan lewat chatbot.\n\n"
            "Aku sudah menandai ini untuk HRD. Untuk jalur resminya, hubungi "
            "{kontak_hrd}."
        ),
    ),
    Topik(
        kode="negosiasi_karier",
        urgensi="normal",
        pola=(
            r"\bminta naik gaji\b", r"\bnegosiasi gaji\b", r"\bkapan saya (?:naik|promosi)\b",
            r"\bpromosi saya\b", r"\bkenaikan gaji saya\b",
        ),
        balasan=(
            "Soal gaji dan jenjang karier pribadimu adalah pembicaraan antara kamu, "
            "atasan, dan HRD — bukan sesuatu yang bisa aku jawab.\n\n"
            "Aku bisa menjelaskan aturan umumnya kalau kamu mau. Untuk kasusmu "
            "sendiri, hubungi {kontak_hrd}."
        ),
    ),
)

_POLA_TERKOMPILASI = tuple(
    (t, tuple(re.compile(p, re.IGNORECASE) for p in t.pola)) for t in TOPIK_TERLARANG
)


# Menyatukan deretan huruf tunggal yang terpisah spasi, supaya singkatan yang
# ditulis bertitik tidak melewati pagar pengaman:
#   "s.p. 1"  -> "s p 1"  -> "sp1"
#   "p.h.k."  -> "p h k"  -> "phk"
# Pola ini hanya mengenai token yang benar-benar satu karakter, jadi kata biasa
# seperti "di ke dari" tidak tersentuh.
_HURUF_TERPISAH_RE = re.compile(r"\b(?:\w\s+){1,5}\w\b")


def normalisasi(teks: str) -> str:
    """Rapikan teks sebelum pencocokan pola."""
    teks = unicodedata.normalize("NFKC", teks).lower()
    teks = re.sub(r"[^\w\s]", " ", teks)
    teks = re.sub(r"\s+", " ", teks).strip()
    return _HURUF_TERPISAH_RE.sub(lambda m: m.group(0).replace(" ", ""), teks)


@dataclass
class HasilPemeriksaan:
    diblokir: bool
    topik: Topik | None = None

    def balasan(self, *, kontak_hrd: str, kontak_krisis: str) -> str:
        assert self.topik is not None
        return self.topik.balasan.format(
            kontak_hrd=kontak_hrd, kontak_krisis=kontak_krisis
        )


def periksa_pertanyaan(pertanyaan: str) -> HasilPemeriksaan:
    teks = normalisasi(pertanyaan)
    for topik, pola in _POLA_TERKOMPILASI:
        if any(p.search(teks) for p in pola):
            return HasilPemeriksaan(diblokir=True, topik=topik)
    return HasilPemeriksaan(diblokir=False)


# --- Redaksi PII sebelum menulis log ---------------------------------------

_REDAKSI = (
    (re.compile(r"\b\d{16}\b"), "[NIK-DIREDAKSI]"),
    (re.compile(r"\b(?:\+?62|0)8\d{7,12}\b"), "[HP-DIREDAKSI]"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b"), "[EMAIL-DIREDAKSI]"),
    (re.compile(r"\b\d{10,16}\b"), "[NOMOR-DIREDAKSI]"),
)


def redaksi_pii(teks: str) -> str:
    """Hilangkan PII yang jelas sebelum disimpan ke log.

    Bukan pengganti kebijakan retensi — hanya mengurangi PII yang tidak
    perlu ikut tersimpan. Lihat README bagian UU PDP.
    """
    for pola, ganti in _REDAKSI:
        teks = pola.sub(ganti, teks)
    return teks
