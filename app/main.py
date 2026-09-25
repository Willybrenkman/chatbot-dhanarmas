"""Aplikasi FastAPI: endpoint karyawan + konsol HRD.

Catatan keamanan MVP: konsol HRD dilindungi satu token bersama lewat header
X-HRD-Token. Ini cukup untuk pilot internal, TAPI wajib diganti SSO sebelum
dipakai luas — lihat README bagian "Sebelum produksi".
"""

from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .chat import ChatService
from .config import Settings, get_settings
from .db import Database
from .models import (
    ApproveRequest,
    ChatRequest,
    ChatResponse,
    HandleEscalationRequest,
    RejectRequest,
)
from .providers import build_provider

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
)
log = logging.getLogger("hrd_chatbot")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

_service: ChatService | None = None
_settings: Settings | None = None


def get_service() -> ChatService:
    if _service is None:  # pragma: no cover - hanya jika lifespan tidak jalan
        raise HTTPException(status_code=503, detail="Layanan belum siap")
    return _service


def get_app_settings() -> Settings:
    if _settings is None:  # pragma: no cover
        raise HTTPException(status_code=503, detail="Konfigurasi belum siap")
    return _settings


def require_hrd(
    x_hrd_token: str = Header(default=""),
    settings: Settings = Depends(get_app_settings),
) -> None:
    """Gerbang konsol HRD. Perbandingan konstan-waktu agar token tidak bisa ditebak bertahap."""
    if not secrets.compare_digest(x_hrd_token, settings.hrd_console_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token HRD tidak valid"
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _service, _settings
    _settings = get_settings()
    db = Database(_settings.database_path)
    provider = build_provider(_settings)
    _service = ChatService(settings=_settings, db=db, provider=provider)

    log.info(
        "Siap. provider=%s model=%s mode=%s dokumen=%d (~%d token)",
        provider.name, provider.model, _settings.answer_mode,
        len(_service.corpus.documents), _service.corpus.perkiraan_token,
    )
    if _settings.hrd_console_token == "ganti-token-ini":
        log.warning("HRD_CONSOLE_TOKEN masih nilai bawaan. Ganti sebelum dipakai.")
    if _service.korpus_memblokir_auto:
        log.warning("Mode auto diminta tapi korpus masih dokumen contoh -> jawaban tetap ditahan sebagai draf.")
    yield


app = FastAPI(title="Asisten HRD - MVP", version="0.1.0", lifespan=lifespan)


# ---------- karyawan ----------

@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, svc: ChatService = Depends(get_service)) -> ChatResponse:
    hasil = await svc.handle_turn(
        session_id=req.session_id,
        pertanyaan=req.pesan,
        employee_ref=req.employee_ref,
        employee_status=req.status_kerja,
        channel=req.channel,
    )
    return ChatResponse(
        hasil=hasil.hasil.value,
        pesan=hasil.pesan_untuk_karyawan,
        disclaimer=hasil.disclaimer,
        sumber=hasil.sumber,
        draft_id=hasil.draft_id,
        escalation_id=hasil.escalation_id,
        kategori_eskalasi=hasil.kategori_eskalasi,
    )


# ---------- konsol HRD ----------

@app.get("/api/hrd/drafts", dependencies=[Depends(require_hrd)])
async def list_drafts(
    status_filter: str = "pending", svc: ChatService = Depends(get_service)
) -> dict:
    return {"drafts": svc.db.list_drafts(status_filter)}


@app.post("/api/hrd/drafts/{draft_id}/approve", dependencies=[Depends(require_hrd)])
async def approve_draft(
    draft_id: int, req: ApproveRequest, svc: ChatService = Depends(get_service)
) -> dict:
    row = svc.db.review_draft(
        draft_id, status="approved", reviewer=req.reviewer, final_answer=req.final_answer
    )
    if row is None:
        raise HTTPException(404, "Draf tidak ditemukan atau sudah ditinjau")
    # Jawaban final = versi editan HRD kalau ada, kalau tidak draf apa adanya.
    jawaban = row["final_answer"] or row["draft_answer"]
    log.info("Draf %s disetujui oleh %s", draft_id, req.reviewer)
    return {
        "draft": row,
        "jawaban_terkirim": jawaban,
        "disclaimer": svc.settings.disclaimer,
        "catatan": (
            "MVP: pengiriman ke karyawan belum otomatis. Jawaban ini "
            "ditampilkan untuk dikirim manual, atau sambungkan channel "
            "WhatsApp/email di app/channels/."
        ),
    }


@app.post("/api/hrd/drafts/{draft_id}/reject", dependencies=[Depends(require_hrd)])
async def reject_draft(
    draft_id: int, req: RejectRequest, svc: ChatService = Depends(get_service)
) -> dict:
    row = svc.db.review_draft(
        draft_id, status="rejected", reviewer=req.reviewer, reject_reason=req.reject_reason
    )
    if row is None:
        raise HTTPException(404, "Draf tidak ditemukan atau sudah ditinjau")
    # Alasan penolakan adalah bahan paling berharga untuk memperbaiki prompt
    # dan dokumen. Ekspor berkala ke eval set.
    log.info("Draf %s ditolak oleh %s: %s", draft_id, req.reviewer, req.reject_reason)
    return {"draft": row}


@app.get("/api/hrd/escalations", dependencies=[Depends(require_hrd)])
async def list_escalations(
    status_filter: str = "open", svc: ChatService = Depends(get_service)
) -> dict:
    return {"escalations": svc.db.list_escalations(status_filter)}


@app.post("/api/hrd/escalations/{esc_id}/handle", dependencies=[Depends(require_hrd)])
async def handle_escalation(
    esc_id: int, req: HandleEscalationRequest, svc: ChatService = Depends(get_service)
) -> dict:
    if not svc.db.handle_escalation(esc_id, req.handled_by, req.note):
        raise HTTPException(404, "Eskalasi tidak ditemukan atau sudah ditangani")
    return {"ok": True}


@app.get("/api/hrd/stats", dependencies=[Depends(require_hrd)])
async def stats(svc: ChatService = Depends(get_service)) -> dict:
    return svc.db.stats()


@app.post("/api/hrd/reload-knowledge", dependencies=[Depends(require_hrd)])
async def reload_knowledge(svc: ChatService = Depends(get_service)) -> dict:
    """Muat ulang dokumen kebijakan tanpa restart, setelah HRD memperbarui file."""
    masalah = svc.reload_knowledge()
    return {
        "dokumen": svc.corpus.filenames,
        "perkiraan_token": svc.corpus.perkiraan_token,
        "masalah": masalah,
    }


@app.get("/api/hrd/onboarding", dependencies=[Depends(require_hrd)])
async def onboarding_report(svc: ChatService = Depends(get_service)) -> dict:
    return {"pengiriman": svc.db.onboarding_report()}


# ---------- kesehatan & halaman ----------

@app.get("/healthz")
async def healthz(svc: ChatService = Depends(get_service)) -> JSONResponse:
    sehat = bool(svc.corpus.documents)
    return JSONResponse(
        status_code=200 if sehat else 503,
        content={
            "status": "ok" if sehat else "korpus_kosong",
            "provider": svc.provider.name,
            "model": svc.provider.model,
            "answer_mode": svc.settings.answer_mode,
            "dokumen": len(svc.corpus.documents),
            "perkiraan_token_korpus": svc.corpus.perkiraan_token,
            "dokumen_contoh": svc.corpus.sample_docs,
            "masalah_korpus": svc.masalah_korpus,
        },
    )


@app.get("/")
async def halaman_chat() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/hrd")
async def halaman_hrd() -> FileResponse:
    return FileResponse(STATIC_DIR / "hrd.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
