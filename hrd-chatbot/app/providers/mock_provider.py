"""Penyedia tiruan: tanpa panggilan jaringan, tanpa API key.

Gunanya nyata, bukan sekadar stub:
  - seluruh alur (guardrail -> prompt -> jawaban -> draf -> log) bisa diuji
    dan didemokan offline
  - tes otomatis jalan di CI tanpa biaya dan tanpa kunci rahasia

Cara kerjanya sederhana: mencari kata kunci pertanyaan di dalam korpus yang
ada di prompt. Kalau ketemu, kembalikan potongan itu dengan sitasi; kalau
tidak, kembalikan sentinel TIDAK_DITEMUKAN. Cukup untuk menguji pipa,
BUKAN untuk menilai kualitas jawaban.

BATAS YANG DIKETAHUI: pencocokan kata kunci masih bisa "menemukan" jawaban
untuk pertanyaan yang sebenarnya tidak ada di korpus, kalau pertanyaannya
memakai kosakata yang sama dengan dokumen (misal "subsidi gym" cocok dengan
"subsidi makan"). Karena itu eval/run_eval.py melewatkan kasus 'jawab' dan
'tidak_ditemukan' saat provider ini aktif, alih-alih melaporkannya gagal.
Jangan memperbaiki heuristik ini — perbaiki dengan memakai provider nyata.
"""

from __future__ import annotations

import re
import time

from ..prompts import SENTINEL_TIDAK_DITEMUKAN
from .base import LLMResponse

STOPWORDS = {
    "apa", "apakah", "bagaimana", "berapa", "kapan", "di", "ke", "dari", "yang",
    "untuk", "dengan", "saya", "aku", "kamu", "itu", "ini", "dan", "atau",
    "bisa", "boleh", "harus", "kalau", "jika", "mau", "ada", "cara", "saja",
    "nya", "tolong", "mohon", "sih", "dong", "ya",
}

DOC_RE = re.compile(r"^===== DOKUMEN: (\S+?)(?: \[.*?\])? =====$", re.MULTILINE)
HEADING_RE = re.compile(r"^#{2,4}\s+(.+)$", re.MULTILINE)


class MockProvider:
    name = "mock"

    def __init__(self, model: str = "mock-1") -> None:
        self.model = model

    @staticmethod
    def _kata_kunci(pertanyaan: str) -> list[str]:
        kata = re.findall(r"\w+", pertanyaan.lower())
        return [k for k in kata if len(k) > 3 and k not in STOPWORDS]

    @staticmethod
    def _potong_dokumen(korpus: str) -> list[tuple[str, str]]:
        """Pecah blok korpus jadi [(nama_file, isi), ...]."""
        batas = [(m.start(), m.group(1)) for m in DOC_RE.finditer(korpus)]
        hasil = []
        for i, (pos, nama) in enumerate(batas):
            akhir = batas[i + 1][0] if i + 1 < len(batas) else len(korpus)
            hasil.append((nama, korpus[pos:akhir]))
        return hasil

    async def complete(
        self,
        *,
        stable_system: str,
        volatile_system: str,
        messages: list[dict[str, str]],
        max_output_tokens: int,
    ) -> LLMResponse:
        mulai = time.perf_counter()
        pertanyaan = next(
            (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
        )
        kunci = self._kata_kunci(pertanyaan)

        dokumen = self._potong_dokumen(stable_system)

        # Kata yang muncul di lebih dari separuh dokumen (misal "perusahaan",
        # "karyawan", "hari") tidak membedakan apa pun, jadi diabaikan.
        if dokumen:
            df = {
                k: sum(1 for _, isi in dokumen if k in isi.lower()) for k in kunci
            }
            kunci = [k for k in kunci if df[k] <= len(dokumen) / 2]

        terbaik: tuple[int, str, str] | None = None
        for nama, isi in self._potong_dokumen(stable_system):
            rendah = isi.lower()
            cocok = [k for k in kunci if k in rendah]
            # Butuh minimal 2 kata kunci khas; satu kecocokan terlalu mudah
            # menghasilkan jawaban palsu.
            if len(cocok) < 2:
                continue
            skor = sum(rendah.count(k) for k in cocok)
            if terbaik is None or skor > terbaik[0]:
                judul = HEADING_RE.search(isi)
                terbaik = (skor, nama, judul.group(1) if judul else "umum")

        if terbaik is None:
            teks = (
                f"{SENTINEL_TIDAK_DITEMUKAN}\n"
                "Aku tidak menemukan informasi ini di dokumen kebijakan yang aku punya."
            )
        else:
            _, nama, bagian = terbaik
            teks = (
                f"[jawaban tiruan untuk pengujian pipa] Informasi yang kamu tanyakan "
                f"ada di dokumen kebijakan. [sumber: {nama} § {bagian}]"
            )

        return LLMResponse(
            text=teks,
            provider=self.name,
            model=self.model,
            input_tokens=len(stable_system) // 4,
            output_tokens=len(teks) // 4,
            cached_tokens=0,
            latency_ms=int((time.perf_counter() - mulai) * 1000),
        )
