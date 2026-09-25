"""Pemilihan kanal pengiriman."""

from __future__ import annotations

import os

from .base import Channel, HasilKirim
from .console import ConsoleChannel

__all__ = ["Channel", "HasilKirim", "ConsoleChannel", "build_channel"]


def build_channel(nama: str) -> Channel:
    """Bangun kanal. Bawaan 'console' (latihan kering, tidak mengirim apa pun)."""
    if nama == "whatsapp":
        from .whatsapp import WhatsAppChannel

        pid = os.environ.get("WA_PHONE_NUMBER_ID", "")
        tok = os.environ.get("WA_ACCESS_TOKEN", "")
        if not pid or not tok:
            raise RuntimeError(
                "Kanal whatsapp butuh WA_PHONE_NUMBER_ID dan WA_ACCESS_TOKEN. "
                "Belum ada kredensial? Pakai --kanal console untuk latihan kering."
            )
        return WhatsAppChannel(phone_number_id=pid, access_token=tok)
    return ConsoleChannel()
