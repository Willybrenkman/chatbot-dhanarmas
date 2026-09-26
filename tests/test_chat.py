"""Uji pipa satu putaran percakapan, dengan provider yang dikendalikan penuh."""

from pathlib import Path

import pytest

from app.chat import ChatService, Hasil
from app.config import Settings
from app.db import Database
from app.providers.base import LLMResponse, ProviderError

KNOWLEDGE = Path(__file__).resolve().parent.parent / "knowledge"


class ProviderPalsu:
    """Provider yang mengembalikan teks yang sudah ditentukan."""

    name = "palsu"
    model = "palsu-1"
    supports_cache = True

    def __init__(self, teks: str) -> None:
        self.teks = teks
        self.panggilan = 0
        self.stable_terakhir = ""
        self.volatile_terakhir = ""

    async def complete(self, *, stable_system, volatile_system, messages, max_output_tokens):
        self.panggilan += 1
        self.stable_terakhir = stable_system
        self.volatile_terakhir = volatile_system
        return LLMResponse(
            text=self.teks, provider=self.name, model=self.model,
            input_tokens=100, output_tokens=20, cached_tokens=90,
        )


class ProviderMeledak:
    """Gagal dengan pengecualian tak terklasifikasi."""

    name = "meledak"
    model = "-"
    supports_cache = False

    async def complete(self, **_):
        raise RuntimeError("endpoint mati")


class ProviderGagalTerklasifikasi:
    """Gagal dengan ProviderError, seperti Groq saat kena batas laju."""

    name = "groq"
    model = "llama-3.3-70b-versatile"
    supports_cache = False

    def __init__(self, kode: str, retry_after_s: float | None = None) -> None:
        self.kode = kode
        self.retry_after_s = retry_after_s

    async def complete(self, **_):
        raise ProviderError(self.kode, f"uji {self.kode}", retry_after_s=self.retry_after_s)


def buat(tmp_path, teks, *, answer_mode="draft", allow_sample=True):
    settings = Settings(
        knowledge_dir=KNOWLEDGE,
        database_path=tmp_path / "t.sqlite3",
        answer_mode=answer_mode,
        allow_sample_docs_in_auto=allow_sample,
        llm_provider="mock",
    )
    provider = ProviderPalsu(teks) if isinstance(teks, str) else teks
    svc = ChatService(settings=settings, db=Database(settings.database_path), provider=provider)
    return svc, provider


JAWABAN_SAH = "Cuti tahunan 12 hari kerja. [sumber: 20-cuti-dan-izin.md § Pasal 1]"


@pytest.mark.asyncio
async def test_mode_draft_menahan_jawaban(tmp_path):
    svc, _ = buat(tmp_path, JAWABAN_SAH)
    h = await svc.handle_turn(session_id="s1", pertanyaan="Cuti berapa hari?")
    assert h.hasil is Hasil.DITAHAN_DRAF
    assert h.draft_id is not None
    assert h.sumber == ["20-cuti-dan-izin.md"]
    # Karyawan tidak boleh melihat jawaban mentah di mode draf.
    assert JAWABAN_SAH not in h.pesan_untuk_karyawan


@pytest.mark.asyncio
async def test_mode_auto_mengirim_langsung(tmp_path):
    svc, _ = buat(tmp_path, JAWABAN_SAH, answer_mode="auto")
    h = await svc.handle_turn(session_id="s1", pertanyaan="Cuti berapa hari?")
    assert h.hasil is Hasil.DIJAWAB
    assert h.pesan_untuk_karyawan == JAWABAN_SAH
    assert h.disclaimer


@pytest.mark.asyncio
async def test_mode_auto_ditolak_kalau_korpus_masih_contoh(tmp_path):
    """Pengaman: data demo tidak boleh terkirim sebagai aturan resmi."""
    svc, _ = buat(tmp_path, JAWABAN_SAH, answer_mode="auto", allow_sample=False)
    assert svc.korpus_memblokir_auto
    h = await svc.handle_turn(session_id="s1", pertanyaan="Cuti berapa hari?")
    assert h.hasil is Hasil.DITAHAN_DRAF


@pytest.mark.asyncio
async def test_sitasi_palsu_dipaksa_lewat_tinjauan(tmp_path):
    """Model menyebut dokumen yang tidak ada -> jangan pernah kirim otomatis."""
    svc, _ = buat(
        tmp_path,
        "Cuti 99 hari. [sumber: dokumen-yang-tidak-ada.md § Pasal 1]",
        answer_mode="auto",
    )
    h = await svc.handle_turn(session_id="s1", pertanyaan="Cuti berapa hari?")
    assert h.hasil is Hasil.DITAHAN_DRAF
    assert h.sumber == []


@pytest.mark.asyncio
async def test_jawaban_tanpa_sitasi_dipaksa_lewat_tinjauan(tmp_path):
    svc, _ = buat(tmp_path, "Cuti tahunan 12 hari kerja.", answer_mode="auto")
    h = await svc.handle_turn(session_id="s1", pertanyaan="Cuti berapa hari?")
    assert h.hasil is Hasil.DITAHAN_DRAF


@pytest.mark.asyncio
async def test_sentinel_tidak_ditemukan_jadi_eskalasi(tmp_path):
    svc, _ = buat(tmp_path, "TIDAK_DITEMUKAN\nTidak ada di dokumen.", answer_mode="auto")
    h = await svc.handle_turn(session_id="s1", pertanyaan="Ada subsidi gym?")
    assert h.hasil is Hasil.DIESKALASI
    assert h.kategori_eskalasi == "tidak_ditemukan"
    assert not h.found_answer


@pytest.mark.asyncio
async def test_topik_terlarang_tidak_memanggil_llm(tmp_path):
    """Hemat biaya, dan yang lebih penting: keputusan keamanan tidak diserahkan ke model."""
    svc, provider = buat(tmp_path, JAWABAN_SAH)
    h = await svc.handle_turn(session_id="s1", pertanyaan="Berapa pesangon saya kalau PHK?")
    assert h.hasil is Hasil.DIESKALASI
    assert h.kategori_eskalasi == "phk_pesangon"
    assert provider.panggilan == 0, "LLM tidak boleh dipanggil untuk topik terlarang"


@pytest.mark.asyncio
async def test_llm_gagal_jadi_eskalasi_bukan_error(tmp_path):
    """Pengecualian tak terduga pun harus jadi eskalasi, bukan 500."""
    svc, _ = buat(tmp_path, ProviderMeledak())
    h = await svc.handle_turn(session_id="s1", pertanyaan="Cuti berapa hari?")
    assert h.hasil is Hasil.DIESKALASI
    assert h.kategori_eskalasi == "galat_lain"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kode,urgensi",
    [
        ("rate_limited", "normal"),
        ("auth", "tinggi"),
        ("model_tidak_ada", "tinggi"),
        ("konteks_penuh", "normal"),
        ("lain", "tinggi"),
    ],
)
async def test_galat_penyedia_dipetakan_per_jenis(tmp_path, kode, urgensi):
    svc, _ = buat(tmp_path, ProviderGagalTerklasifikasi(kode))
    h = await svc.handle_turn(session_id="s1", pertanyaan="Cuti berapa hari?")
    assert h.hasil is Hasil.DIESKALASI
    assert h.kategori_eskalasi == f"galat_{kode}"
    esc = svc.db.list_escalations()
    assert esc[0]["urgency"] == urgensi


@pytest.mark.asyncio
async def test_batas_laju_tidak_mengarahkan_ke_hrd(tmp_path):
    """Batas laju berlalu sendiri; menyuruh karyawan menelepon HRD cuma bikin beban."""
    svc, _ = buat(tmp_path, ProviderGagalTerklasifikasi("rate_limited", retry_after_s=7.5))
    h = await svc.handle_turn(session_id="s1", pertanyaan="Cuti berapa hari?")
    assert "hubungi" not in h.pesan_untuk_karyawan.lower()
    assert "8 detik" in h.pesan_untuk_karyawan


@pytest.mark.asyncio
async def test_galat_konfigurasi_mengarahkan_ke_hrd(tmp_path):
    svc, _ = buat(tmp_path, ProviderGagalTerklasifikasi("auth"))
    h = await svc.handle_turn(session_id="s1", pertanyaan="Cuti berapa hari?")
    assert "hubungi" in h.pesan_untuk_karyawan.lower()
    # Jangan bocorkan detail teknis ke karyawan.
    assert "api" not in h.pesan_untuk_karyawan.lower()


@pytest.mark.asyncio
async def test_pii_diredaksi_sebelum_masuk_log(tmp_path):
    svc, _ = buat(tmp_path, JAWABAN_SAH)
    await svc.handle_turn(
        session_id="s1", pertanyaan="NIK saya 3201234567890123, cuti berapa?"
    )
    pesan = svc.db.recent_messages("s1", 10)
    tersimpan = " ".join(m["content"] for m in pesan)
    assert "3201234567890123" not in tersimpan
    assert "[NIK-DIREDAKSI]" in tersimpan


@pytest.mark.asyncio
async def test_blok_stabil_identik_antar_status_kerja(tmp_path):
    """Properti yang menentukan biaya: prompt cache tidak boleh batal karena
    status kerja penanya berbeda. Kalau gagal, korpus di-cache ulang tiap status."""
    svc, provider = buat(tmp_path, JAWABAN_SAH)

    await svc.handle_turn(session_id="s1", pertanyaan="Cuti berapa?", employee_status="pkwt")
    stabil_pkwt, volatil_pkwt = provider.stable_terakhir, provider.volatile_terakhir

    await svc.handle_turn(session_id="s2", pertanyaan="Cuti berapa?", employee_status="tetap")

    assert provider.stable_terakhir == stabil_pkwt, "blok stabil berubah antar status"
    assert provider.volatile_terakhir != volatil_pkwt, "blok volatil seharusnya berbeda"
    assert "PKWT" in volatil_pkwt


@pytest.mark.asyncio
async def test_riwayat_percakapan_terbawa(tmp_path):
    svc, provider = buat(tmp_path, JAWABAN_SAH)
    await svc.handle_turn(session_id="s1", pertanyaan="Cuti berapa hari?")
    await svc.handle_turn(session_id="s1", pertanyaan="Kalau PKWT?")
    riwayat = svc.db.recent_messages("s1", 10)
    assert len(riwayat) == 4  # 2 tanya + 2 jawab
    assert riwayat[0]["content"] == "Cuti berapa hari?"


@pytest.mark.asyncio
async def test_token_tercatat_untuk_audit_biaya(tmp_path):
    svc, _ = buat(tmp_path, JAWABAN_SAH)
    await svc.handle_turn(session_id="s1", pertanyaan="Cuti berapa hari?")
    t = svc.db.stats()["token"]
    assert t["input"] == 100 and t["output"] == 20 and t["cache_read"] == 90


@pytest.mark.asyncio
async def test_reload_knowledge_tanpa_restart(tmp_path):
    svc, _ = buat(tmp_path, JAWABAN_SAH)
    sebelum = len(svc.corpus.documents)
    svc.reload_knowledge()
    assert len(svc.corpus.documents) == sebelum


# --- Tipografi Unicode dari model terbuka ---------------------------------
#
# gpt-oss-120b lewat Groq menyisipkan narrow no-break space (U+202F) di antara
# angka dan satuannya, dan sesekali tepat setelah kurung buka sitasi. Di layar
# tidak ada bedanya dengan spasi biasa, tapi setiap pencocokan teks meleset.
# Efeknya paling mahal di sitasi: jawaban yang sudah menyitasi dengan benar
# dianggap tanpa sumber, lalu ditahan untuk ditinjau manusia tanpa alasan.

JAWABAN_SPASI_ANEH = (
    "Jam kerja 08.00 sampai 17.00, istirahat 12.00‑"
    "13.00. [ sumber: 10-jam-kerja-dan-absensi.md § Pasal 1]"
)


def test_normalisasi_menyamakan_spasi_dan_hubung_unicode():
    from app.chat import normalisasi_teks

    hasil = normalisasi_teks(JAWABAN_SPASI_ANEH)
    assert " " not in hasil
    assert "‑" not in hasil
    assert "08.00 sampai 17.00" in hasil
    assert "12.00-13.00" in hasil


def test_normalisasi_membiarkan_en_dash():
    from app.chat import normalisasi_teks

    assert normalisasi_teks("Senin – Jumat") == "Senin – Jumat"


@pytest.mark.asyncio
async def test_sitasi_tetap_terbaca_walau_model_pakai_spasi_unicode(tmp_path):
    svc, _ = buat(tmp_path, JAWABAN_SPASI_ANEH)
    hasil = await svc.handle_turn(session_id="s1", pertanyaan="Jam kerja kapan?")
    assert hasil.sumber == ["10-jam-kerja-dan-absensi.md"]
    assert hasil.hasil is Hasil.DITAHAN_DRAF


@pytest.mark.asyncio
async def test_teks_tersimpan_sudah_dinormalkan(tmp_path):
    svc, _ = buat(tmp_path, JAWABAN_SPASI_ANEH)
    await svc.handle_turn(session_id="s1", pertanyaan="Jam kerja kapan?")
    jawaban = svc.db.recent_messages("s1", 10)[-1]["content"]
    assert " " not in jawaban
