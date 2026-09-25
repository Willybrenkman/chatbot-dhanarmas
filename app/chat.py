"""Pipa satu putaran percakapan.

Urutannya sengaja eksplisit dan dipisah dari pemanggilan LLM, supaya nanti
menambah tool (cek sisa cuti, ajukan cuti) masuk di celah antara langkah 5
dan 6 tanpa menulis ulang apa pun — ini salah satu dari tiga aturan yang
menjaga MVP ini tidak jadi jalan buntu.

    1. pastikan sesi ada
    2. simpan pertanyaan (PII diredaksi)
    3. PAGAR PENGAMAN deterministik  -> kalau kena, eskalasi, TANPA panggil LLM
    4. susun prompt (blok stabil yang di-cache + blok volatil)
    5. ambil riwayat percakapan
    6. panggil LLM                    <- di sini nanti tool call masuk
    7. periksa hasil: sentinel, sitasi, sitasi palsu
    8. tentukan mode: jawab langsung / tahan sebagai draf / eskalasi
    9. simpan jejak audit
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum

from .config import Settings
from .db import Database
from .guardrails import periksa_pertanyaan, redaksi_pii
from .knowledge import Corpus, load_corpus, periksa_korpus
from .prompts import (
    SENTINEL_TIDAK_DITEMUKAN,
    build_stable_block,
    build_volatile_block,
)
from .providers import LLMProvider, ProviderError

log = logging.getLogger(__name__)

SITASI_RE = re.compile(r"\[sumber:\s*([^\]§]+?)\s*§\s*([^\]]+?)\]", re.IGNORECASE)


class Hasil(str, Enum):
    DIJAWAB = "dijawab"
    DITAHAN_DRAF = "ditahan_draf"
    DIESKALASI = "dieskalasi"


PESAN_DITAHAN = (
    "Terima kasih, pertanyaanmu sudah aku catat. Jawabannya sedang ditinjau "
    "oleh tim HRD dulu dan akan dikirim ke kamu sebentar lagi."
)

PESAN_TIDAK_DITEMUKAN = (
    "Maaf, aku tidak menemukan jawabannya di dokumen kebijakan yang aku punya, "
    "jadi aku tidak mau menebak.\n\nPertanyaanmu sudah aku teruskan ke HRD "
    "supaya dijawab dengan benar."
)

# Pesan per jenis kegagalan penyedia. Yang dilihat karyawan harus jujur tanpa
# membocorkan detail teknis; kategori eskalasi yang membedakannya untuk HRD.
PESAN_GALAT = {
    "rate_limited": (
        "Maaf, aku sedang menerima banyak pertanyaan sekaligus. Coba kirim lagi "
        "sebentar lagi ya.{jeda}"
    ),
    "auth": (
        "Maaf, ada masalah konfigurasi di sistemku sehingga aku tidak bisa "
        "menjawab sekarang. Ini sudah ditandai untuk tim teknis."
    ),
    "konteks_penuh": (
        "Maaf, pertanyaan ini terlalu panjang untuk aku proses. Coba pecah jadi "
        "pertanyaan yang lebih pendek."
    ),
    "model_tidak_ada": (
        "Maaf, ada masalah konfigurasi di sistemku sehingga aku tidak bisa "
        "menjawab sekarang. Ini sudah ditandai untuk tim teknis."
    ),
    "lain": (
        "Maaf, sedang ada gangguan teknis di sistemku. Pertanyaanmu sudah aku "
        "teruskan ke HRD."
    ),
}

# Galat konfigurasi adalah masalah tim teknis, bukan HRD, jadi diberi urgensi
# tinggi supaya cepat terlihat; batas laju adalah kondisi normal yang berlalu.
URGENSI_GALAT = {
    "rate_limited": "normal",
    "auth": "tinggi",
    "model_tidak_ada": "tinggi",
    "konteks_penuh": "normal",
    "lain": "tinggi",
}


@dataclass
class JawabanChat:
    hasil: Hasil
    pesan_untuk_karyawan: str
    disclaimer: str | None = None
    sumber: list[str] = field(default_factory=list)
    found_answer: bool = False
    draft_id: int | None = None
    escalation_id: int | None = None
    kategori_eskalasi: str | None = None
    # Untuk konsol HRD / debugging — tidak dikirim ke karyawan di mode draf.
    jawaban_mentah: str | None = None


class ChatService:
    def __init__(
        self, *, settings: Settings, db: Database, provider: LLMProvider
    ) -> None:
        self.settings = settings
        self.db = db
        self.provider = provider
        self.corpus: Corpus = Corpus()
        self.masalah_korpus: list[str] = []
        self._stable_block: str = ""
        self.reload_knowledge()

    # ---------- korpus ----------

    def reload_knowledge(self) -> list[str]:
        """Muat ulang dokumen kebijakan tanpa restart aplikasi."""
        self.corpus = load_corpus(self.settings.knowledge_dir)
        self.masalah_korpus = periksa_korpus(
            self.corpus,
            answer_mode=self.settings.answer_mode,
            allow_sample=self.settings.allow_sample_docs_in_auto,
            ambang_token=self.settings.ambang_token_korpus,
        )
        self._stable_block = build_stable_block(
            nama_bot=self.settings.nama_bot,
            nama_perusahaan=self.settings.nama_perusahaan,
            korpus=self.corpus.as_prompt_block(),
            daftar_dokumen=self.corpus.daftar_dokumen(),
        )
        for m in self.masalah_korpus:
            log.warning("Korpus: %s", m)
        return self.masalah_korpus

    @property
    def korpus_memblokir_auto(self) -> bool:
        """True kalau mode auto tidak boleh dijalankan dengan korpus saat ini."""
        return any(m.startswith("Mode 'auto' ditolak") for m in self.masalah_korpus)

    # ---------- pemeriksaan jawaban ----------

    def _periksa_sitasi(self, teks: str) -> tuple[list[str], list[str]]:
        """Kembalikan (sitasi_sah, sitasi_palsu).

        Sitasi palsu = model menyebut nama dokumen yang tidak ada di korpus.
        Ini indikasi halusinasi dan harus menghentikan pengiriman otomatis.
        """
        dikenal = set(self.corpus.filenames)
        sah: list[str] = []
        palsu: list[str] = []
        for berkas, _bagian in SITASI_RE.findall(teks):
            nama = berkas.strip()
            (sah if nama in dikenal else palsu).append(nama)
        return sorted(set(sah)), sorted(set(palsu))

    def _galat_penyedia(
        self,
        session_id: str,
        pertanyaan_log: str,
        *,
        kode: str,
        retry_after_s: float | None = None,
    ) -> JawabanChat:
        """Catat kegagalan penyedia dan susun pesan yang sesuai jenisnya."""
        s = self.settings
        jeda = ""
        if kode == "rate_limited" and retry_after_s:
            jeda = f" Kira-kira {int(retry_after_s) + 1} detik lagi."
        pesan = PESAN_GALAT.get(kode, PESAN_GALAT["lain"]).format(jeda=jeda)

        # Batas laju bukan hal yang perlu dibawa ke HRD — ia berlalu sendiri.
        if kode != "rate_limited":
            pesan += f"\n\nKalau mendesak, hubungi {s.kontak_hrd}."

        esc_id = self.db.add_escalation(
            session_id, pertanyaan_log, f"galat_{kode}", URGENSI_GALAT.get(kode, "tinggi")
        )
        self.db.add_message(
            session_id, "assistant", pesan, found_answer=False, provider="error"
        )
        return JawabanChat(
            hasil=Hasil.DIESKALASI,
            pesan_untuk_karyawan=pesan,
            escalation_id=esc_id,
            kategori_eskalasi=f"galat_{kode}",
        )

    # ---------- alur utama ----------

    async def handle_turn(
        self,
        *,
        session_id: str,
        pertanyaan: str,
        employee_ref: str | None = None,
        employee_status: str = "tidak_diketahui",
        channel: str = "web",
    ) -> JawabanChat:
        s = self.settings
        pertanyaan = pertanyaan.strip()

        # 1-2. sesi + simpan pertanyaan (PII diredaksi sebelum masuk log)
        self.db.ensure_session(
            session_id,
            employee_ref=employee_ref,
            employee_status=employee_status,
            channel=channel,
        )
        pertanyaan_log = redaksi_pii(pertanyaan)
        self.db.add_message(session_id, "user", pertanyaan_log)

        # 3. pagar pengaman deterministik — sebelum model dipanggil
        cek = periksa_pertanyaan(pertanyaan)
        if cek.diblokir:
            assert cek.topik is not None
            balasan = cek.balasan(
                kontak_hrd=s.kontak_hrd, kontak_krisis=s.kontak_krisis
            )
            esc_id = self.db.add_escalation(
                session_id, pertanyaan_log, cek.topik.kode, cek.topik.urgensi
            )
            self.db.add_message(
                session_id, "assistant", balasan, found_answer=False, provider="guardrail"
            )
            log.info(
                "Pertanyaan diblokir pagar pengaman. topik=%s urgensi=%s",
                cek.topik.kode, cek.topik.urgensi,
            )
            return JawabanChat(
                hasil=Hasil.DIESKALASI,
                pesan_untuk_karyawan=balasan,
                escalation_id=esc_id,
                kategori_eskalasi=cek.topik.kode,
                found_answer=False,
            )

        # 4-5. prompt + riwayat
        volatile = build_volatile_block(employee_status)
        riwayat = self.db.recent_messages(session_id, s.history_turns)
        # Pesan terakhir adalah pertanyaan ini sendiri; provider butuh ia ada
        # di posisi terakhir, jadi riwayat dipakai apa adanya.
        if not riwayat or riwayat[-1]["role"] != "user":
            riwayat = [*riwayat, {"role": "user", "content": pertanyaan_log}]

        # 6. panggil LLM
        try:
            resp = await self.provider.complete(
                stable_system=self._stable_block,
                volatile_system=volatile,
                messages=riwayat,
                max_output_tokens=s.max_output_tokens,
            )
        except ProviderError as galat:
            log.error("Penyedia gagal. kode=%s: %s", galat.kode, galat)
            return self._galat_penyedia(
                session_id, pertanyaan_log, kode=galat.kode,
                retry_after_s=galat.retry_after_s,
            )
        except Exception:
            log.exception("Panggilan LLM gagal tanpa klasifikasi")
            return self._galat_penyedia(session_id, pertanyaan_log, kode="lain")

        # 7. periksa hasil
        teks = resp.text.strip()
        tidak_ditemukan = teks.upper().startswith(SENTINEL_TIDAK_DITEMUKAN)
        sitasi_sah, sitasi_palsu = self._periksa_sitasi(teks)

        if sitasi_palsu:
            log.warning(
                "Model menyitasi dokumen yang tidak ada: %s", ", ".join(sitasi_palsu)
            )

        msg_id = self.db.add_message(
            session_id,
            "assistant",
            teks,
            sources=sitasi_sah or None,
            found_answer=not tidak_ditemukan,
            provider=resp.provider,
            model=resp.model,
            input_tokens=resp.input_tokens,
            output_tokens=resp.output_tokens,
            cached_tokens=resp.cached_tokens,
            latency_ms=resp.latency_ms,
        )

        # 8a. tidak ditemukan -> eskalasi, jangan kirim tebakan
        if tidak_ditemukan:
            esc_id = self.db.add_escalation(
                session_id, pertanyaan_log, "tidak_ditemukan", "normal"
            )
            return JawabanChat(
                hasil=Hasil.DIESKALASI,
                pesan_untuk_karyawan=PESAN_TIDAK_DITEMUKAN,
                escalation_id=esc_id,
                kategori_eskalasi="tidak_ditemukan",
                found_answer=False,
                jawaban_mentah=teks,
            )

        # 8b. jawaban meragukan (tanpa sitasi sah, atau ada sitasi palsu)
        #     dipaksa lewat tinjauan manusia walau mode auto.
        ragu = (not sitasi_sah) or bool(sitasi_palsu)

        if s.answer_mode == "auto" and not ragu and not self.korpus_memblokir_auto:
            return JawabanChat(
                hasil=Hasil.DIJAWAB,
                pesan_untuk_karyawan=teks,
                disclaimer=s.disclaimer,
                sumber=sitasi_sah,
                found_answer=True,
                jawaban_mentah=teks,
            )

        draft_id = self.db.add_draft(session_id, msg_id, pertanyaan_log, teks)
        if ragu:
            log.info("Jawaban ditahan karena meragukan. draft_id=%s", draft_id)
        return JawabanChat(
            hasil=Hasil.DITAHAN_DRAF,
            pesan_untuk_karyawan=PESAN_DITAHAN,
            sumber=sitasi_sah,
            found_answer=True,
            draft_id=draft_id,
            jawaban_mentah=teks,
        )
