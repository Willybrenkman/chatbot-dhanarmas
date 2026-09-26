# Asisten HRD — MVP chatbot tanya-jawab kebijakan

Bot tanya-jawab kebijakan kepegawaian untuk karyawan **PT Dhanarmas**, dengan
fokus **karyawan baru**. Lingkupnya sengaja sempit: Tier 1 saja — menjawab dari
dokumen kebijakan, tanpa integrasi HRIS, tanpa transaksi.

Yang ada di sini:

| | |
|---|---|
| Tanya-jawab kebijakan | jawaban wajib bersitasi pasal, tanpa RAG (seluruh dokumen masuk context) |
| Mode draf | bot menyiapkan jawaban, HRD meninjau & mengirim — risiko nol di minggu pertama |
| Pagar pengaman | 9 kategori topik yang tidak boleh dijawab bot, diperiksa deterministik sebelum LLM dipanggil |
| Konsol HRD | tinjau draf, tangani eskalasi, lihat statistik & status dokumen |
| Eval set | 49 kasus + runner, supaya perbaikan bisa diukur bukan ditebak |
| Onboarding terjadwal | 6 pesan dalam 90 hari, tanpa integrasi HRIS (cukup CSV) |
| Empat penyedia LLM | mock (offline), Groq (tier gratis), Claude API, dan model self-hosted |

## Jalan dalam 2 menit

```bash
python3 -m pip install -r requirements.txt
cp .env.example .env            # bawaan: provider mock, mode draft
uvicorn app.main:app --reload --port 8081
```

- Chat karyawan → http://localhost:8081
- Konsol HRD → http://localhost:8081/hrd (token dari `HRD_CONSOLE_TOKEN`)
- Kesehatan sistem → http://localhost:8081/healthz

Bawaannya `LLM_PROVIDER=mock`, jadi jalan **tanpa API key dan tanpa jaringan**.
Provider tiruan tidak menjawab sungguhan — gunanya menguji seluruh alur
(pagar pengaman → prompt → draf → log) secara offline.

Untuk jawaban sungguhan:

```bash
# di .env
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
```

## Uji

```bash
python3 -m pytest -q              # 89 tes, tanpa jaringan
bash scripts/smoke.sh             # verifikasi menyeluruh
python3 scripts/cek_provider.py   # cek kunci API & nama model penyedia
python3 eval/run_eval.py          # eval set (mock: hanya pagar pengaman)
LLM_PROVIDER=groq python3 eval/run_eval.py --ambang 85
```

## Arsitektur

```
Karyawan (web / WhatsApp)
        │
        ▼
   app/main.py ── FastAPI, gerbang token konsol HRD
        │
        ▼
   app/chat.py ── pipa satu putaran:
        │  1. pastikan sesi
        │  2. simpan pertanyaan (PII diredaksi)
        │  3. PAGAR PENGAMAN deterministik → kena? eskalasi, LLM tidak dipanggil
        │  4. susun prompt (blok stabil di-cache + blok volatil)
        │  5. ambil riwayat
        │  6. panggil LLM            ← nanti tool call masuk DI SINI
        │  7. periksa sentinel, sitasi, sitasi palsu
        │  8. mode: jawab / tahan sebagai draf / eskalasi
        │  9. simpan jejak audit
        ▼
   app/providers/ ── mock | anthropic | groq | hermes
   app/db.py      ── sessions, messages, drafts, escalations, onboarding
```

Orchestrator dan n8n **tidak ada di sini, dengan sengaja**. Keduanya ada untuk
menjadi penjaga gerbang antara model dan tool. Tidak ada tool berarti tidak ada
yang perlu dijaga. Arsitekturnya mengecil sesuai kebutuhan, bukan kehilangan
sesuatu yang penting.

## Tiga aturan yang menjaga ini tidak jadi jalan buntu

Kekhawatiran wajar: "kalau nanti mau ditambah tool, apa harus tulis ulang?"
Tidak — tiga hal ini dijaga sejak awal, dan semuanya gratis sekarang tapi mahal
kalau di-retrofit:

1. **Percakapan tersimpan terstruktur**, bukan log teks — `sessions`,
   `messages`, `drafts`, `escalations` di `app/db.py`, dengan jejak token per
   pesan untuk audit biaya.
2. **"Susun konteks" dan "panggil LLM" adalah dua fungsi terpisah.** Tool call
   nanti masuk tepat di celah antara langkah 5 dan 6 di `app/chat.py`.
3. **Logika di kode, bukan di prompt.** Pagar pengaman berjalan sebelum model
   dipanggil, di `app/guardrails.py`. Prompt tetap diberi aturan yang sama
   sebagai lapisan kedua, bukan satu-satunya.

Menambahkan orchestrator + n8n nanti adalah **penambahan, bukan penulisan ulang**.

## Kenapa tanpa RAG

Seluruh korpus kebijakan dimasukkan ke `system` dan di-cache, bukan dipotong
dan diambil sebagian.

| | RAG | Seluruh dokumen di context |
|---|---|---|
| Pekerjaan | chunking, embedding, tuning retrieval | tempel dokumen, selesai |
| Bug khas | potongan relevan tidak terambil | tidak ada |
| Akurasi | tergantung kualitas retrieval | lebih tinggi — tidak ada yang terlewat |
| Biaya | lebih murah di volume tinggi | lebih mahal di volume tinggi |

Untuk pilot ini menang jelas: hemat 1–2 minggu kerja dan akurasinya lebih baik.

**Kapan pindah ke RAG:** `app/knowledge.py` memperingatkan otomatis kalau
korpus melewati ~400 ribu token perkiraan. Titik balik biayanya sekitar
8.000 percakapan/bulan. Saat itu tiba, ganti `build_corpus()` — pemanggilnya
tidak perlu berubah.

Korpus contoh saat ini: 6 dokumen, ~3.600 token. Masih sangat jauh dari batas.

## Prompt caching

Korpus ada di blok `system` **pertama** dengan `cache_control` ttl 1 jam.
Konteks per-sesi (status kerja penanya) ada di blok **kedua** yang tidak
di-cache.

Ini bukan detail kosmetik. Caching bekerja dengan pencocokan prefiks — satu
byte berubah di awal membatalkan seluruh cache sesudahnya. Kalau status kerja
digabung ke blok yang sama, setiap status memicu cache write baru untuk korpus
yang bisa ratusan ribu token. `tests/test_chat.py` menguji properti ini secara
langsung.

**Cara memeriksa cache benar-benar kena:** lihat `cache_read` di
`/api/hrd/stats` atau tab Statistik di konsol HRD. Kalau nol terus padahal
sudah banyak permintaan, ada yang membatalkan cache — biasanya timestamp atau
ID yang ikut masuk prefiks prompt.

## Pagar pengaman

Sembilan kategori diblokir **sebelum** LLM dipanggil, secara deterministik.
Keamanan tidak dititipkan ke prompt.

| Kategori | Urgensi |
|---|---|
| `krisis` (isyarat menyakiti diri) | kritis |
| `pelecehan_kekerasan` | kritis |
| `phk_pesangon` | tinggi |
| `sanksi_disiplin` | tinggi |
| `sengketa_hukum` | tinggi |
| `whistleblowing` | tinggi |
| `data_orang_lain` | normal |
| `konflik_personal` | normal |
| `negosiasi_karier` | normal |

Aturan praktisnya: **kalau sebuah jawaban bisa dijadikan bukti dalam
perselisihan hubungan industrial, itu bukan pekerjaan bot.**

Kasus `tolak` di eval set **wajib 100%**. Runner menandainya khusus karena ini
soal keselamatan, bukan kualitas.

## Tiga pengaman lain yang mudah dilewatkan

1. **Dokumen contoh tidak bisa dipakai di mode auto.** Semua berkas di
   `knowledge/` bertanda `contoh: true`. Mode `auto` akan menolak dan tetap
   menahan jawaban sebagai draf sampai dokumen resmi masuk. Ini mencegah data
   demo terkirim ke karyawan sebagai aturan resmi.
2. **Sitasi palsu menghentikan pengiriman otomatis.** Kalau model menyebut nama
   dokumen yang tidak ada di korpus, jawaban dipaksa lewat tinjauan manusia
   walau mode `auto`. Begitu juga jawaban tanpa sitasi sama sekali.
3. **Sentinel `TIDAK_DITEMUKAN`.** Model wajib memakainya kalau jawaban tidak
   ada di dokumen. Bot mengaku tidak tahu dan mengeskalasi, bukan menebak.

## Mengganti dokumen contoh dengan dokumen resmi

1. Hapus berkas di `knowledge/`, ganti dengan dokumen resmi HRD sebagai `.md`
2. Beri prefiks angka untuk mengatur urutan (`20-cuti-dan-izin.md`)
3. Isi front matter — `berlaku_sejak` penting, ini yang membuat bot bisa
   menyebutkan tanggal berlaku saat dokumen mungkin sudah usang:

```markdown
---
judul: Cuti, Izin, dan Ketidakhadiran
berlaku_sejak: 2026-01-01
pemilik: HRD - Operations
versi: 2.0
---

## Pasal 1 Cuti Tahunan
Karyawan tetap berhak atas 12 hari kerja...
```

4. **Hapus `contoh: true`** — ini yang membuka mode auto
5. Muat ulang tanpa restart: tombol di konsol HRD, atau
   `POST /api/hrd/reload-knowledge`

Gunakan judul pasal yang jelas dan konsisten. Itulah yang disitasi bot, dan
karyawan akan memakainya untuk mengecek ke HRD.

## Eval set

49 kasus di `eval/eval_set.yaml`: 38 `jawab`, 8 `tolak`, 3 `tidak_ditemukan`.

Ini baru **kerangka** berdasarkan dokumen contoh. Sebelum rilis, HRD harus:

1. Mengganti pertanyaannya dengan pertanyaan **nyata** dari log mereka — minta
   ekspor 200 tiket/chat terakhir, ambil yang paling sering muncul
2. Mengisi jawaban benar dan sumbernya sendiri
3. Menambah sampai minimal 50 kasus, porsi `tolak` minimal 15%

Penilaiannya deterministik (cocokkan kata kunci + sumber), bukan LLM judge —
murah, jalan di CI, hasilnya tidak berubah antar-jalan. Kelemahannya jelas:
parafrase yang benar bisa dinilai salah. Kalau nanti butuh penilaian lebih
halus, tambahkan LLM judge **sebagai lapisan kedua** untuk kasus yang gagal,
jangan menggantikan yang ini.

Dengan `LLM_PROVIDER=mock`, runner hanya menilai kasus `tolak` dan melewatkan
sisanya — menilai isi jawaban dengan provider tiruan tidak bermakna, dan
melaporkannya gagal cuma jadi derau.

## Onboarding terjadwal

Lihat `onboarding/README.md`. Ringkasnya: 6 pesan dalam 90 hari, sumber data
satu CSV yang HRD isi, tidak butuh integrasi HRIS.

Onboarding juga **cara termurah mulai memakai WhatsApp**: audiensnya hanya
karyawan baru (puluhan per bulan, bukan 5000), jadi biaya template kecil.
Kanalnya terbukti dulu di skala kecil, baru dibuka ke semua karyawan.

## Pilihan penyedia LLM

Empat pilihan, ditukar lewat satu variabel di `.env`. Aplikasi hanya bicara ke
antarmuka di `app/providers/base.py`, jadi tidak ada kode lain yang berubah.

| `LLM_PROVIDER` | Untuk apa | Prompt caching |
|---|---|---|
| `mock` | uji & demo offline, tanpa API key | — |
| `groq` | **pilihan proyek ini** — model terbuka, ada tier gratis | tidak ada |
| `anthropic` | opsional, kalau kepatuhan sitasi perlu lebih tinggi | ada, ttl 1 jam |
| `hermes` | model self-hosted (vLLM), saat data tidak boleh keluar | prefix caching vLLM |

`groq` dan `hermes` memakai kelas yang sama (`app/providers/openai_compatible.py`)
karena bentuk API-nya identik — yang beda cuma base URL, nama model, dan kunci.

Bawaan yang di-commit tetap `mock`, supaya klon baru bisa jalan tanpa kunci apa
pun. Ganti ke `groq` di `.env` lokal kamu (yang tidak ikut ter-commit).

**Kalau dijalankan di sesi cloud dan panggilan gagal dengan 403 di tahap CONNECT:**
itu kebijakan jaringan environment yang menolak `api.groq.com`, bukan kunci atau
kode yang salah. Izinkan host itu di setelan Network access environment tersebut,
atau jalankan di mesin sendiri.

### Groq (tier gratis)

```bash
# 1. ambil kunci gratis di https://console.groq.com/keys, taruh di .env:
#      LLM_PROVIDER=groq
#      GROQ_API_KEY=gsk_...

# 2. cari nama model yang benar-benar tersedia untuk kuncimu
python3 scripts/cek_provider.py --daftar-model

# 3. salin salah satu id ke GROQ_MODEL di .env, lalu uji satu pertanyaan
python3 scripts/cek_provider.py

# 4. ukur kepatuhannya terhadap eval set
LLM_PROVIDER=groq python3 eval/run_eval.py
```

Langkah 2 bukan formalitas: **nama model Groq berubah dan yang lama dihentikan**,
jadi nilai apa pun yang tertulis di `.env.example` pasti akan basi. Skripnya
membaca daftar dari API, bukan dari daftar yang ditulis di kode.

Tiga hal yang perlu kamu tahu dengan Groq:

- **Tidak ada prompt caching.** Korpus kebijakan dibaca ulang seharga penuh
  setiap turn. Dengan korpus contoh ini (~5.000 token per permintaan) itu tidak
  masalah, tapi jadi penting kalau dokumen kalian tumbuh besar.
- **Batas laju tier gratis akan kena.** Pipa menanganinya sebagai kondisi normal
  yang berlalu: karyawan diberi pesan "coba sebentar lagi" beserta perkiraan
  jeda, dan eskalasinya berurgensi normal — tidak menyuruh menelepon HRD untuk
  sesuatu yang selesai sendiri dalam beberapa detik.
- **Kepatuhan instruksi lebih rapuh** daripada model komersial besar: format
  sitasi dan sentinel `TIDAK_DITEMUKAN` lebih sering dilewatkan. Itu justru
  alasan pemeriksaan sitasi ada — jawaban yang tidak patuh ditahan untuk
  ditinjau manusia, bukan diteruskan ke karyawan. Jalankan eval set untuk
  melihat seberapa sering itu terjadi pada model pilihanmu.

### Cocokkan ambang korpus dengan context window model

`AMBANG_TOKEN_KORPUS` di `.env` harus sesuai context window model yang dipakai,
bukan dibiarkan di bawaannya. Bawaan sekarang 60.000, pas untuk model ber-context
128rb seperti pilihan Groq di atas; naikkan ke 400.000 kalau pindah ke model
ber-context sejuta seperti Claude:

```bash
python3 scripts/cek_provider.py --ukur-korpus
```

Skrip itu menghitung ukuran prompt penuh dan menyebut context window minimal
yang aman. Kalau prompt melewati context model, permintaan gagal dengan kode
`konteks_penuh` dan pipa memberi pesan yang tepat, bukan "gangguan teknis".

### Hermes / vLLM on-prem

```bash
# di .env
LLM_PROVIDER=hermes
HERMES_BASE_URL=http://gpu-server:8000/v1
HERMES_MODEL=NousResearch/Hermes-3-Llama-3.1-8B
```

Catatan kapasitas untuk 5000 karyawan: satu GPU kelas L4 cukup untuk model 8B
pada beban normal, tetapi beban puncak (THR, cuti bersama) bisa 3-5x dan butuh
GPU kedua. Model 70B - yang jauh lebih andal mengikuti instruksi sitasi - butuh
A100/H100.

Pola hibrida yang disarankan: penyedia terkelola untuk tanya-jawab kebijakan
(tidak menyentuh PII), model on-prem untuk apa pun yang membaca data pribadi
karyawan nanti.

### Kalau penyedia gagal

Kegagalan diklasifikasi, bukan diseragamkan jadi "gangguan teknis". Tiap jenis
mendapat pesan yang tepat untuk karyawan dan urgensi eskalasi yang sesuai:

| Kode | Pesan ke karyawan | Urgensi |
|---|---|---|
| `rate_limited` | "coba sebentar lagi" + perkiraan jeda | normal |
| `auth` | masalah konfigurasi, sudah ditandai tim teknis | tinggi |
| `model_tidak_ada` | sama seperti di atas | tinggi |
| `konteks_penuh` | minta pertanyaan dipecah lebih pendek | normal |
| `lain` | gangguan teknis, diteruskan ke HRD | tinggi |

Detail teknis tidak pernah ditampilkan ke karyawan; itu hanya masuk log dan
kategori eskalasi.

## Sebelum produksi

Ini MVP pilot. Yang **wajib** dibereskan sebelum dipakai luas:

| Hal | Sekarang | Harus jadi |
|---|---|---|
| Auth konsol HRD | satu token bersama di header | SSO, dengan peran per-unit |
| Identitas karyawan | tidak ada (tidak perlu, karena tidak ada data pribadi) | **wajib terverifikasi** sebelum menambah tool apa pun yang membaca data karyawan |
| Basis data | SQLite | Postgres (skemanya sudah disiapkan untuk pindah) |
| Retensi log | tidak terbatas | kebijakan retensi + penghapusan berkala (UU PDP) |
| Dokumen | contoh fiktif | dokumen resmi HRD, `contoh: true` dihapus |
| HTTPS | tidak | wajib, di depan reverse proxy |
| Rate limit | tidak ada | per sesi dan per IP |

## Catatan UU PDP 27/2022

- **PII diredaksi sebelum masuk log** (`app/guardrails.py`): NIK, nomor HP,
  email, nomor panjang. Ini mengurangi PII yang tidak perlu tersimpan, **bukan**
  pengganti kebijakan retensi.
- Korpus kebijakan tidak memuat data pribadi siapa pun, jadi prompt yang
  dikirim ke penyedia LLM tidak membawa data karyawan — kecuali kalau penanya
  sendiri menuliskannya. Karena itu redaksi dilakukan di sisi log, dan halaman
  chat memberi peringatan agar tidak mengirim data pribadi.
- Untuk pesan onboarding ke nomor pribadi (terutama pra-masuk H-3), siapkan
  dasar persetujuan — titipkan klausulnya di offer letter atau kontrak.
- **Transkrip juga tersimpan di browser karyawan** (`sessionStorage`), supaya refresh
  tidak terlihat seperti kehilangan percakapan. Umurnya sebatas tab itu — tutup tab,
  hilang — dan tidak pernah dikirim ke mana pun. Di komputer bersama, ingatkan
  karyawan menutup tabnya; itu sekaligus alasan riwayat TIDAK ditarik ulang dari
  server, karena kalau begitu menutup tab tidak lagi cukup.
- Tetapkan kebijakan retensi log percakapan. Belum ada di MVP ini.

## Struktur berkas

```
.
├── app/
│   ├── main.py            FastAPI, endpoint, gerbang token HRD
│   ├── chat.py            pipa satu putaran percakapan
│   ├── guardrails.py      9 kategori topik terlarang + redaksi PII
│   ├── knowledge.py       pemuat korpus + pengaman dokumen contoh
│   ├── prompts.py         system prompt, pemisahan blok stabil/volatil
│   ├── config.py          konfigurasi dari .env
│   ├── db.py              SQLite, skema siap pindah ke Postgres
│   ├── models.py          skema permintaan/respons
│   ├── providers/         mock | anthropic | groq | hermes
│   └── channels/          console (latihan kering) | whatsapp
├── knowledge/             6 dokumen kebijakan CONTOH — ganti dengan yang resmi
├── static/                index.html (chat karyawan), hrd.html (konsol HRD)
├── eval/                  eval_set.yaml (49 kasus) + run_eval.py
├── onboarding/            scheduler.py, messages.yaml, contoh CSV
├── tests/                 89 tes
└── scripts/
    ├── smoke.sh        verifikasi menyeluruh
    └── cek_provider.py  cek kunci API, nama model, ukuran korpus
```

## Yang belum dikerjakan

Disebutkan terang-terangan supaya tidak jadi kejutan:

- **Pengiriman jawaban yang disetujui belum otomatis.** Konsol HRD menampilkan
  jawaban final untuk dikirim manual. Menyambungkannya butuh kanal balik
  (WhatsApp webhook atau email) — kerangkanya ada di `app/channels/`.
- **Kanal WhatsApp belum pernah diuji** terhadap endpoint sungguhan; sesi
  pengembangan ini tidak punya kredensial Meta/BSP. Bentuk permintaannya
  mengikuti dokumentasi Cloud API. Uji ke nomor sendiri lebih dulu.
- **Belum ada workflow n8n.** Penjadwal onboarding ditulis sebagai skrip Python
  karena workflow n8n tidak bisa divalidasi tanpa akses instance kalian. Bentuk
  pemindahannya ada di `onboarding/README.md`.
- **Tidak ada RAG.** Disengaja — lihat "Kenapa tanpa RAG".
- **Tidak ada Tier 2/3** (cek sisa cuti, ajukan cuti). Disengaja.
