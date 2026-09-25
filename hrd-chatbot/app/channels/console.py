"""Kanal konsol: menulis ke log, tidak mengirim apa pun.

Bawaan untuk pilot dan latihan kering (dry run). Pakai ini untuk memeriksa
isi dan jadwal pesan SEBELUM menyambungkan WhatsApp — sekali pesan terkirim
ke 5000 nomor, tidak bisa ditarik kembali.
"""

from __future__ import annotations

import logging

from .base import HasilKirim

log = logging.getLogger(__name__)


class ConsoleChannel:
    name = "console"

    async def kirim(
        self, *, tujuan: str, isi: str, template: str | None = None
    ) -> HasilKirim:
        garis = "-" * 64
        log.info(
            "\n%s\n[LATIHAN KERING] ke=%s template=%s\n%s\n%s",
            garis, tujuan, template or "-", isi.strip(), garis,
        )
        return HasilKirim(berhasil=True, kanal=self.name, ref="dry-run")
