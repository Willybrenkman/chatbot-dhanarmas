"""Kontrak kanal pengiriman pesan keluar."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class HasilKirim:
    berhasil: bool
    kanal: str
    error: str | None = None
    ref: str | None = None


@runtime_checkable
class Channel(Protocol):
    name: str

    async def kirim(
        self, *, tujuan: str, isi: str, template: str | None = None
    ) -> HasilKirim:
        """Kirim satu pesan.

        tujuan   : nomor HP (format 62xxx) atau alamat email
        isi      : teks pesan yang sudah jadi
        template : nama template WhatsApp yang sudah disetujui Meta.
                   Wajib untuk pesan keluar di luar jendela layanan 24 jam.
        """
        ...
