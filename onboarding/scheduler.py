#!/usr/bin/env python3
"""Penjadwal pesan onboarding karyawan baru.

Tidak butuh integrasi HRIS. Sumber datanya satu berkas CSV yang HRD isi
(ekspor dari Google Sheet juga bisa). Jalankan sekali sehari lewat cron:

    # setiap hari kerja pukul 08.10
    10 8 * * 1-5 cd /path/ke/repo && python3 onboarding/scheduler.py --jalankan

Perintah:
    --impor BERKAS.csv          muat/perbarui daftar karyawan baru
    --jalankan                  kirim pesan yang jatuh tempo hari ini
    --kanal console|whatsapp    bawaan console (latihan kering, tidak mengirim)
    --tanggal YYYY-MM-DD        anggap hari ini tanggal tersebut (untuk simulasi)
    --laporan                   tampilkan riwayat pengiriman
    --ulangi-tertunda JAM       kirim ulang yang macet di status pending
    --praktinjau                tampilkan yang AKAN dikirim, tanpa mengklaim

URUTAN KLAIM LALU KIRIM (penting):
    Baris pengiriman diklaim di basis data LEBIH DULU, baru pesannya dikirim.
    Artinya kalau proses mati di antara keduanya, pesan itu TIDAK terkirim dan
    tidak akan dicoba ulang otomatis — macet di status 'pending'.

    Ini dipilih sadar: di skala 5000 karyawan, pesan ganda ke nomor pribadi
    lebih merusak kepercayaan daripada pesan yang telat. Pemulihannya manual
    dan disengaja: jalankan --ulangi-tertunda 6 untuk mengirim ulang baris yang
    macet lebih dari 6 jam, setelah kamu pastikan memang belum terkirim.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.channels import build_channel  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import Database  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
log = logging.getLogger("onboarding")

LANGKAH_PATH = Path(__file__).parent / "messages.yaml"
KOLOM_WAJIB = {"employee_ref", "nama", "tanggal_mulai"}


def muat_langkah() -> list[dict]:
    langkah = yaml.safe_load(LANGKAH_PATH.read_text(encoding="utf-8"))
    return sorted(langkah, key=lambda s: s["offset_hari"])


def impor_csv(db: Database, berkas: Path) -> int:
    with berkas.open(encoding="utf-8-sig", newline="") as f:
        baris = list(csv.DictReader(f))
    if not baris:
        log.warning("Berkas %s kosong", berkas.name)
        return 0

    kurang = KOLOM_WAJIB - set(baris[0].keys())
    if kurang:
        raise SystemExit(f"Kolom wajib tidak ada di CSV: {', '.join(sorted(kurang))}")

    jumlah = 0
    for i, r in enumerate(baris, 2):  # baris 1 = header
        ref = (r.get("employee_ref") or "").strip()
        nama = (r.get("nama") or "").strip()
        mulai = (r.get("tanggal_mulai") or "").strip()
        if not (ref and nama and mulai):
            log.warning("Baris %d dilewati: employee_ref/nama/tanggal_mulai kosong", i)
            continue
        try:
            date.fromisoformat(mulai)
        except ValueError:
            log.warning("Baris %d dilewati: tanggal_mulai '%s' bukan format YYYY-MM-DD", i, mulai)
            continue
        db.upsert_enrollment(
            {
                "employee_ref": ref,
                "nama": nama,
                "phone": (r.get("phone") or "").strip() or None,
                "email": (r.get("email") or "").strip() or None,
                "divisi": (r.get("divisi") or "").strip() or None,
                "status_kerja": (r.get("status_kerja") or "").strip() or None,
                "tanggal_mulai": mulai,
                "atasan_phone": (r.get("atasan_phone") or "").strip() or None,
            }
        )
        jumlah += 1
    log.info("Impor selesai: %d karyawan baru dimuat/diperbarui", jumlah)
    return jumlah


def render(isi: str, e: dict, kontak_hrd: str) -> str:
    return isi.format(
        nama=e["nama"].split()[0],
        divisi=e.get("divisi") or "unitmu",
        tanggal_mulai=e["tanggal_mulai"],
        kontak_hrd=kontak_hrd,
    )


def jatuh_tempo(e: dict, langkah: dict, hari_ini: date) -> date:
    return date.fromisoformat(e["tanggal_mulai"]) + timedelta(days=langkah["offset_hari"])


async def jalankan(
    db: Database, *, kanal_nama: str, hari_ini: date, praktinjau: bool, kontak_hrd: str
) -> None:
    langkah_semua = muat_langkah()
    kanal = build_channel(kanal_nama) if not praktinjau else None

    calon: list[tuple[dict, dict]] = []
    for e in db.active_enrollments():
        for s in langkah_semua:
            if jatuh_tempo(e, s, hari_ini) == hari_ini:
                calon.append((e, s))

    if not calon:
        log.info("Tidak ada pesan yang jatuh tempo pada %s", hari_ini)
        return

    log.info("%d pesan jatuh tempo pada %s", len(calon), hari_ini)

    for e, s in calon:
        label = f"{e['nama']} / {s['step_id']}"

        if praktinjau:
            print(f"\n=== AKAN DIKIRIM: {label} ===")
            print(f"tujuan: {e.get('phone') or '(nomor kosong!)'}  template: {s.get('template','-')}")
            print(render(s["isi"], e, kontak_hrd))
            continue

        # Klaim lebih dulu -> idempoten. False berarti sudah pernah diklaim.
        if not db.claim_send(e["id"], s["step_id"], hari_ini.isoformat()):
            log.info("Dilewati (sudah pernah diklaim): %s", label)
            continue

        tujuan = e.get("phone")
        if not tujuan:
            log.warning("Tidak ada nomor HP untuk %s", label)
            db.mark_send(e["id"], s["step_id"], status="failed",
                         channel=kanal_nama, error="nomor HP kosong")
            continue

        isi = render(s["isi"], e, kontak_hrd)
        hasil = await kanal.kirim(tujuan=tujuan, isi=isi, template=s.get("template"))
        db.mark_send(
            e["id"], s["step_id"],
            status="sent" if hasil.berhasil else "failed",
            channel=hasil.kanal, error=hasil.error,
        )
        log.info("%s: %s", "TERKIRIM" if hasil.berhasil else "GAGAL", label)

        # Pengingat masa percobaan juga ke atasan.
        if s.get("kirim_ke_atasan") and e.get("atasan_phone"):
            step_atasan = s["step_id"] + "_atasan"
            if db.claim_send(e["id"], step_atasan, hari_ini.isoformat()):
                isi_atasan = (
                    f"Pengingat: masa percobaan {e['nama']} "
                    f"({e.get('divisi') or '-'}) berakhir sekitar 30 hari lagi. "
                    f"Mohon siapkan evaluasi bulan ke-3. Info: {kontak_hrd}"
                )
                h2 = await kanal.kirim(
                    tujuan=e["atasan_phone"], isi=isi_atasan, template=s.get("template")
                )
                db.mark_send(
                    e["id"], step_atasan,
                    status="sent" if h2.berhasil else "failed",
                    channel=h2.kanal, error=h2.error,
                )
                log.info("%s: %s (ke atasan)",
                         "TERKIRIM" if h2.berhasil else "GAGAL", label)


async def ulangi_tertunda(db: Database, *, jam: int, kanal_nama: str, kontak_hrd: str) -> None:
    """Kirim ulang baris yang macet di 'pending' lebih lama dari `jam`.

    Dipakai setelah proses mati di antara klaim dan kirim. Periksa dulu bahwa
    pesannya memang belum sampai — alat ini tidak bisa tahu.
    """
    batas = datetime.now(timezone.utc) - timedelta(hours=jam)
    langkah_map = {s["step_id"]: s for s in muat_langkah()}
    kanal = build_channel(kanal_nama)

    with db.connect() as conn:
        baris = conn.execute(
            """SELECT s.enrollment_id, s.step_id, s.scheduled_for, e.*
                 FROM onboarding_sends s
                 JOIN onboarding_enrollments e ON e.id = s.enrollment_id
                WHERE s.status = 'pending' AND s.scheduled_for <= ?""",
            (batas.date().isoformat(),),
        ).fetchall()

    if not baris:
        log.info("Tidak ada pengiriman yang macet lebih dari %d jam", jam)
        return

    log.warning("%d pengiriman macet akan dicoba ulang", len(baris))
    for r in baris:
        e = dict(r)
        s = langkah_map.get(e["step_id"].replace("_atasan", ""))
        if not s or not e.get("phone"):
            db.mark_send(e["enrollment_id"], e["step_id"], status="failed",
                         channel=kanal_nama, error="langkah/nomor tidak valid saat ulang")
            continue
        hasil = await kanal.kirim(
            tujuan=e["phone"], isi=render(s["isi"], e, kontak_hrd), template=s.get("template")
        )
        db.mark_send(
            e["enrollment_id"], e["step_id"],
            status="sent" if hasil.berhasil else "failed",
            channel=hasil.kanal, error=hasil.error,
        )
        log.info("Ulang %s: %s", e["step_id"], "TERKIRIM" if hasil.berhasil else "GAGAL")


def laporan(db: Database) -> None:
    baris = db.onboarding_report()
    if not baris:
        print("Belum ada riwayat pengiriman.")
        return
    print(f"{'Nama':22s} {'Langkah':18s} {'Status':8s} {'Jadwal':12s} Kanal")
    print("-" * 78)
    for r in baris:
        print(f"{(r['nama'] or '')[:21]:22s} {r['step_id']:18s} {r['status']:8s} "
              f"{r['scheduled_for']:12s} {r['channel'] or '-'}"
              + (f"  ERR: {r['error'][:40]}" if r["error"] else ""))


def main() -> int:
    ap = argparse.ArgumentParser(description="Penjadwal pesan onboarding karyawan baru")
    ap.add_argument("--impor", type=Path, metavar="CSV")
    ap.add_argument("--jalankan", action="store_true")
    ap.add_argument("--praktinjau", action="store_true",
                    help="tampilkan yang akan dikirim tanpa mengklaim/mengirim")
    ap.add_argument("--kanal", default="console", choices=["console", "whatsapp"])
    ap.add_argument("--tanggal", help="YYYY-MM-DD, anggap hari ini tanggal ini")
    ap.add_argument("--laporan", action="store_true")
    ap.add_argument("--ulangi-tertunda", type=int, metavar="JAM")
    args = ap.parse_args()

    settings = get_settings()
    db = Database(settings.database_path)
    hari_ini = date.fromisoformat(args.tanggal) if args.tanggal else date.today()

    if args.impor:
        impor_csv(db, args.impor)
    if args.praktinjau or args.jalankan:
        if args.kanal == "whatsapp" and not args.praktinjau:
            log.warning("Mengirim SUNGGUHAN lewat WhatsApp. Pastikan sudah dites ke nomor sendiri.")
        asyncio.run(jalankan(db, kanal_nama=args.kanal, hari_ini=hari_ini,
                             praktinjau=args.praktinjau, kontak_hrd=settings.kontak_hrd))
    if args.ulangi_tertunda:
        asyncio.run(ulangi_tertunda(db, jam=args.ulangi_tertunda,
                                    kanal_nama=args.kanal, kontak_hrd=settings.kontak_hrd))
    if args.laporan:
        laporan(db)
    if not any([args.impor, args.jalankan, args.praktinjau, args.laporan, args.ulangi_tertunda]):
        ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
