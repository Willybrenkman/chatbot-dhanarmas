"""Uji endpoint HTTP: gerbang token, alur draf, dan halaman."""

import pytest
from fastapi.testclient import TestClient

TOKEN = "token-uji-rahasia"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("HRD_CONSOLE_TOKEN", TOKEN)
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "api.sqlite3"))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("ANSWER_MODE", "draft")
    # get_settings di-cache, jadi harus dibersihkan setelah env diubah.
    from app.config import get_settings
    get_settings.cache_clear()
    import app.main as main
    with TestClient(main.app) as c:
        yield c
    get_settings.cache_clear()


H = {"X-HRD-Token": TOKEN}


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "ok"
    assert d["dokumen"] >= 6


def test_halaman_tersedia(client):
    assert client.get("/").status_code == 200
    assert client.get("/hrd").status_code == 200


def test_chat_menahan_draf(client):
    r = client.post("/api/chat", json={
        "session_id": "sesi-api-001", "pesan": "Cuti tahunan berapa hari?"
    })
    assert r.status_code == 200
    assert r.json()["hasil"] == "ditahan_draf"


def test_chat_topik_terlarang_dieskalasi(client):
    r = client.post("/api/chat", json={
        "session_id": "sesi-api-002", "pesan": "Berapa pesangon kalau saya di-PHK?"
    })
    d = r.json()
    assert d["hasil"] == "dieskalasi"
    assert d["kategori_eskalasi"] == "phk_pesangon"


def test_validasi_masukan(client):
    assert client.post("/api/chat", json={"session_id": "x", "pesan": "hai"}).status_code == 422
    assert client.post("/api/chat", json={
        "session_id": "sesi-cukup-panjang", "pesan": ""
    }).status_code == 422
    assert client.post("/api/chat", json={
        "session_id": "sesi-cukup-panjang", "pesan": "hai", "status_kerja": "bukan_status"
    }).status_code == 422


@pytest.mark.parametrize("path", [
    "/api/hrd/drafts", "/api/hrd/escalations", "/api/hrd/stats", "/api/hrd/onboarding",
])
def test_konsol_hrd_butuh_token(client, path):
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"X-HRD-Token": "salah"}).status_code == 401
    assert client.get(path, headers=H).status_code == 200


def test_alur_setujui_draf(client):
    client.post("/api/chat", json={
        "session_id": "sesi-api-003", "pesan": "Cuti tahunan berapa hari?"
    })
    draf = client.get("/api/hrd/drafts", headers=H).json()["drafts"]
    assert draf
    did = draf[0]["id"]

    r = client.post(f"/api/hrd/drafts/{did}/approve", headers=H,
                    json={"reviewer": "budi", "final_answer": "Jawaban hasil edit HRD."})
    assert r.status_code == 200
    assert r.json()["jawaban_terkirim"] == "Jawaban hasil edit HRD."

    # Tinjauan kedua harus ditolak — mencegah kirim ganda.
    assert client.post(f"/api/hrd/drafts/{did}/approve", headers=H,
                       json={"reviewer": "budi"}).status_code == 404


def test_alur_tolak_draf(client):
    client.post("/api/chat", json={
        "session_id": "sesi-api-004", "pesan": "Slip gaji ambil di mana?"
    })
    did = client.get("/api/hrd/drafts", headers=H).json()["drafts"][0]["id"]
    r = client.post(f"/api/hrd/drafts/{did}/reject", headers=H,
                    json={"reviewer": "siti", "reject_reason": "sitasi pasalnya salah"})
    assert r.status_code == 200
    assert r.json()["draft"]["status"] == "rejected"
    assert not client.get("/api/hrd/drafts", headers=H).json()["drafts"]


def test_tangani_eskalasi(client):
    client.post("/api/chat", json={
        "session_id": "sesi-api-005", "pesan": "Saya dilecehkan rekan kerja"
    })
    esc = client.get("/api/hrd/escalations", headers=H).json()["escalations"]
    assert esc and esc[0]["urgency"] == "kritis"
    eid = esc[0]["id"]
    assert client.post(f"/api/hrd/escalations/{eid}/handle", headers=H,
                       json={"handled_by": "hrd", "note": "sudah ditindaklanjuti"}).status_code == 200
    assert client.post(f"/api/hrd/escalations/{eid}/handle", headers=H,
                       json={"handled_by": "hrd"}).status_code == 404


def test_eskalasi_kritis_diurutkan_paling_atas(client):
    r1 = client.post("/api/chat", json={
        "session_id": "sesi-urut-a", "pesan": "Saya mau minta naik gaji"})
    r2 = client.post("/api/chat", json={
        "session_id": "sesi-urut-b", "pesan": "Saya dilecehkan atasan"})
    assert r1.status_code == 200 and r2.status_code == 200
    esc = client.get("/api/hrd/escalations", headers=H).json()["escalations"]
    assert esc[0]["urgency"] == "kritis"


def test_reload_knowledge(client):
    r = client.post("/api/hrd/reload-knowledge", headers=H)
    assert r.status_code == 200
    assert r.json()["dokumen"]


def test_stats_punya_kunci_yang_dipakai_konsol(client):
    client.post("/api/chat", json={"session_id": "sesi-api-006", "pesan": "Cuti berapa hari?"})
    d = client.get("/api/hrd/stats", headers=H).json()
    for k in ("total_pertanyaan", "persen_terjawab", "tidak_ditemukan",
              "total_sesi", "eskalasi_per_kategori", "draf_per_status", "token"):
        assert k in d
