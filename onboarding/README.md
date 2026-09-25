# Pesan onboarding terjadwal

Enam pesan dalam 90 hari untuk karyawan baru. Tidak butuh integrasi HRIS —
sumber datanya satu berkas CSV yang HRD isi.

## Alur kerja HRD

1. HRD memelihara satu Google Sheet berisi karyawan baru
2. Ekspor sebagai CSV ke `onboarding/karyawan_baru.csv`
3. Cron menjalankan scheduler setiap hari kerja pagi

```bash
# muat/perbarui daftar karyawan baru
python3 onboarding/scheduler.py --impor onboarding/karyawan_baru.csv

# lihat yang AKAN dikirim hari ini, tanpa mengirim & tanpa mengklaim
python3 onboarding/scheduler.py --praktinjau

# kirim (bawaan: latihan kering ke log, tidak mengirim apa pun)
python3 onboarding/scheduler.py --jalankan

# kirim sungguhan lewat WhatsApp
python3 onboarding/scheduler.py --jalankan --kanal whatsapp

# simulasi tanggal lain, untuk memeriksa jadwal
python3 onboarding/scheduler.py --praktinjau --tanggal 2026-10-15

# riwayat pengiriman
python3 onboarding/scheduler.py --laporan
```

Cron yang disarankan:

```cron
10 8 * * 1-5 cd /path/ke/repo && python3 onboarding/scheduler.py --impor onboarding/karyawan_baru.csv --jalankan --kanal whatsapp >> logs/onboarding.log 2>&1
```

## Kolom CSV

| Kolom | Wajib | Catatan |
|---|---|---|
| `employee_ref` | ya | ID unik, jadi kunci idempotensi |
| `nama` | ya | nama depan dipakai untuk menyapa |
| `tanggal_mulai` | ya | `YYYY-MM-DD`, dasar semua perhitungan jadwal |
| `phone` | — | format `62xxx`. Kosong = pesan ditandai gagal, bukan error |
| `email` | — | belum dipakai; siapkan kalau mau kanal email |
| `divisi` | — | dipakai di pesan hari ke-30 |
| `status_kerja` | — | untuk pelaporan |
| `atasan_phone` | — | penerima kedua pengingat masa percobaan H+60 |

Lihat `karyawan_baru.contoh.csv`. Kelima barisnya sengaja disusun agar semua
langkah jatuh tempo pada hari yang sama, supaya jadwalnya mudah diuji.

## Idempotensi dan urutan klaim-lalu-kirim

Tabel `onboarding_sends` punya `UNIQUE(enrollment_id, step_id)`. Baris diklaim
di basis data **lebih dulu**, pesannya dikirim **setelah** itu.

Konsekuensinya disengaja: kalau proses mati di antara keduanya, pesan tidak
terkirim dan **tidak dicoba ulang otomatis** — macet di status `pending`.

Alasannya: di skala 5000 karyawan, pesan ganda ke nomor pribadi lebih merusak
kepercayaan daripada pesan yang telat. Pemulihannya manual dan disengaja:

```bash
# periksa dulu mana yang macet
python3 onboarding/scheduler.py --laporan | grep pending

# setelah yakin memang belum terkirim, kirim ulang yang macet > 6 jam
python3 onboarding/scheduler.py --ulangi-tertunda 6 --kanal whatsapp
```

## Sebelum menyalakan WhatsApp

1. **Template disetujui Meta** untuk setiap pesan keluar. Semua pesan
   onboarding termasuk kategori ini. Persetujuan butuh 1–2 minggu — jangan
   taruh di jalur kritis jadwal rilis.
2. **Dasar persetujuan UU PDP** untuk mengirim ke nomor pribadi, terutama
   pesan pra-masuk H-3. Titipkan klausulnya di offer letter atau kontrak.
3. **Uji ke nomor sendiri lebih dulu.** Kode kanal WhatsApp di
   `app/channels/whatsapp.py` mengikuti dokumentasi Cloud API tetapi **belum
   pernah dijalankan** terhadap endpoint sungguhan.
4. **Jalankan `--praktinjau` dulu**, selalu. Sekali pesan terkirim, tidak bisa
   ditarik kembali.

## Catatan soal pulse check hari ke-30

Pesan hari ke-30 menanyakan kendala. Dua hal yang harus dijaga:

- Katakan eksplisit bahwa jawabannya **untuk membantu, bukan menilai**. Kalau
  karyawan baru curiga jawabannya dipakai untuk evaluasi masa percobaan,
  mereka akan menjawab "semua baik" dan datanya jadi tidak berguna.
- Keluhan soal atasan atau rekan kerja yang muncul di sini **harus masuk jalur
  manusia yang rahasia**, bukan tercatat di log bot bersama pertanyaan soal
  parkir. Pagar pengaman di `app/guardrails.py` sudah mengarahkan topik ini ke
  eskalasi, tapi prosedur tindak lanjutnya tanggung jawab HRD.

## Alternatif n8n

Penjadwal ini sengaja ditulis sebagai skrip Python, bukan workflow n8n, karena
aku tidak bisa memvalidasi JSON workflow n8n tanpa akses ke instance kalian —
dan workflow n8n yang ditulis buta lebih merepotkan daripada tidak ada.

Kalau mau dipindahkan ke n8n nanti, bentuknya:

```
Schedule Trigger (harian)
  -> Google Sheets: read rows
  -> Code: hitung langkah yang jatuh tempo
  -> HTTP Request: POST ke /api/hrd/onboarding/claim   (perlu endpoint baru)
  -> WhatsApp / HTTP Request ke Cloud API
  -> Google Sheets: tulis status
```

Yang **harus tetap di sisi aplikasi**, bukan di n8n: kunci idempotensi. Kalau
klaim pengiriman dipindah ke spreadsheet, kamu kehilangan jaminan UNIQUE dan
pesan ganda cuma soal waktu.
