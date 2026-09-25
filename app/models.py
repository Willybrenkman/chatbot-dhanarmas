"""Skema permintaan dan respons API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

StatusKerja = Literal[
    "tetap", "pkwt", "harian", "outsourcing", "magang", "tidak_diketahui"
]


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=6, max_length=64)
    pesan: str = Field(min_length=1, max_length=2000)
    # Opsional dan dinyatakan sendiri oleh penanya. MVP tidak memverifikasi
    # identitas karena tidak mengakses data pribadi apa pun; kalau nanti ada
    # tool yang membaca data karyawan, ini WAJIB diganti identitas terverifikasi.
    employee_ref: str | None = Field(default=None, max_length=64)
    status_kerja: StatusKerja = "tidak_diketahui"
    channel: Literal["web", "whatsapp", "console"] = "web"


class ChatResponse(BaseModel):
    hasil: Literal["dijawab", "ditahan_draf", "dieskalasi"]
    pesan: str
    disclaimer: str | None = None
    sumber: list[str] = Field(default_factory=list)
    draft_id: int | None = None
    escalation_id: int | None = None
    kategori_eskalasi: str | None = None


class ApproveRequest(BaseModel):
    reviewer: str = Field(min_length=1, max_length=64)
    # Kalau HRD mengedit jawaban, kirim versi finalnya di sini.
    final_answer: str | None = Field(default=None, max_length=8000)


class RejectRequest(BaseModel):
    reviewer: str = Field(min_length=1, max_length=64)
    reject_reason: str = Field(min_length=1, max_length=1000)


class HandleEscalationRequest(BaseModel):
    handled_by: str = Field(min_length=1, max_length=64)
    note: str | None = Field(default=None, max_length=2000)
