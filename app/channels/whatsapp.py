"""Kanal WhatsApp lewat Cloud API milik Meta.

BELUM DIUJI: sesi pengembangan ini tidak punya kredensial BSP/Meta, jadi
bentuk permintaannya mengikuti dokumentasi Cloud API tetapi belum pernah
dijalankan terhadap endpoint sungguhan. Uji dengan satu nomor sendiri
sebelum dipakai untuk siapa pun.

Yang perlu disiapkan sebelum ini bisa dipakai:

1. Nomor WhatsApp Business + akses Cloud API (langsung Meta atau via BSP).
2. TEMPLATE DISETUJUI META untuk setiap pesan keluar di luar jendela
   layanan 24 jam. Semua pesan onboarding masuk kategori ini. Persetujuan
   butuh 1-2 minggu — jangan taruh di jalur kritis jadwal rilis.
3. Dasar persetujuan pemrosesan data (UU PDP) untuk mengirim ke nomor
   pribadi, terutama pesan pra-masuk H-3. Titipkan klausulnya di offer
   letter atau kontrak.

Catatan biaya: balasan di dalam jendela layanan 24 jam umumnya gratis,
yang berbayar adalah template keluar. Karena onboarding hanya menyasar
karyawan baru (puluhan per bulan, bukan 5000), biayanya kecil — inilah cara
termurah menguji kanal WhatsApp sebelum dibuka ke semua karyawan.
"""

from __future__ import annotations

import logging

import httpx

from .base import HasilKirim

log = logging.getLogger(__name__)


class WhatsAppChannel:
    name = "whatsapp"

    def __init__(
        self,
        *,
        phone_number_id: str,
        access_token: str,
        bahasa: str = "id",
        api_version: str = "v21.0",
        timeout_s: float = 20.0,
    ) -> None:
        self.phone_number_id = phone_number_id
        self._token = access_token
        self.bahasa = bahasa
        self.api_version = api_version
        self._timeout = timeout_s

    async def kirim(
        self, *, tujuan: str, isi: str, template: str | None = None
    ) -> HasilKirim:
        url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"

        if template:
            # Pesan keluar di luar jendela 24 jam HARUS memakai template.
            # Isi teks bebas akan ditolak Meta.
            payload = {
                "messaging_product": "whatsapp",
                "to": tujuan,
                "type": "template",
                "template": {
                    "name": template,
                    "language": {"code": self.bahasa},
                    "components": [
                        {
                            "type": "body",
                            "parameters": [{"type": "text", "text": isi.strip()}],
                        }
                    ],
                },
            }
        else:
            payload = {
                "messaging_product": "whatsapp",
                "to": tujuan,
                "type": "text",
                "text": {"body": isi.strip()},
            }

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    url, json=payload,
                    headers={"Authorization": f"Bearer {self._token}"},
                )
            if resp.status_code >= 400:
                # Jangan pernah mencoba ulang secara membuta: pengiriman yang
                # sebenarnya berhasil lalu diulang berarti karyawan dapat
                # pesan dobel. Idempotensi dijaga di tabel onboarding_sends.
                log.error("WhatsApp gagal %s: %s", resp.status_code, resp.text[:300])
                return HasilKirim(
                    berhasil=False, kanal=self.name,
                    error=f"HTTP {resp.status_code}: {resp.text[:200]}",
                )
            data = resp.json()
            ref = (data.get("messages") or [{}])[0].get("id")
            return HasilKirim(berhasil=True, kanal=self.name, ref=ref)
        except Exception as exc:
            log.exception("WhatsApp error jaringan")
            return HasilKirim(berhasil=False, kanal=self.name, error=str(exc))
