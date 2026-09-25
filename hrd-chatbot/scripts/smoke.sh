#!/usr/bin/env bash
# Verifikasi menyeluruh: uji, eval pagar pengaman, dan alur onboarding.
# Jalankan dari root proyek:  bash scripts/smoke.sh
set -euo pipefail
cd "$(dirname "$0")/.."

echo "=== 1/5 uji otomatis ==="
python3 -m pytest -q

echo
echo "=== 2/5 eval pagar pengaman (wajib 100%) ==="
python3 eval/run_eval.py --hanya tolak --ambang 100

echo
echo "=== 3/5 kesehatan korpus ==="
python3 - <<'PY'
import sys; sys.path.insert(0, '.')
from pathlib import Path
from app.knowledge import load_corpus, periksa_korpus
c = load_corpus(Path('knowledge'))
print(f"dokumen: {len(c.documents)}  perkiraan token: {c.perkiraan_token:,}")
print(f"dokumen contoh: {len(c.sample_docs)}")
auto = periksa_korpus(c, answer_mode='auto', allow_sample=False)
blokir = [m for m in auto if m.startswith("Mode 'auto' ditolak")]
print("pengaman mode auto:", "AKTIF (dokumen masih contoh)" if blokir else "tidak memblokir")
PY

echo
echo "=== 4/5 alur onboarding (latihan kering, DB sementara) ==="
TMP=$(mktemp -d)
DATABASE_PATH="$TMP/smoke.sqlite3" python3 onboarding/scheduler.py \
    --impor onboarding/karyawan_baru.contoh.csv 2>&1 | tail -1
DATABASE_PATH="$TMP/smoke.sqlite3" python3 onboarding/scheduler.py \
    --jalankan 2>&1 | grep -cE "TERKIRIM" | xargs -I{} echo "pesan terkirim (latihan kering): {}"
DATABASE_PATH="$TMP/smoke.sqlite3" python3 onboarding/scheduler.py \
    --jalankan 2>&1 | grep -cE "Dilewati" | xargs -I{} echo "idempotensi, jalan kedua dilewati: {}"
rm -rf "$TMP"

echo
echo "=== 5/5 API ujung ke ujung ==="
python3 - <<'PY'
import sys, tempfile, os; sys.path.insert(0, '.')
os.environ.update(HRD_CONSOLE_TOKEN="smoke", LLM_PROVIDER="mock", ANSWER_MODE="draft",
                  DATABASE_PATH=tempfile.mkdtemp() + "/s.sqlite3")
from app.config import get_settings; get_settings.cache_clear()
from fastapi.testclient import TestClient
import app.main as main
H = {"X-HRD-Token": "smoke"}
with TestClient(main.app) as c:
    assert c.get("/healthz").json()["status"] == "ok"
    assert c.post("/api/chat", json={"session_id": "smoke-001",
        "pesan": "Cuti tahunan berapa hari?"}).json()["hasil"] == "ditahan_draf"
    assert c.post("/api/chat", json={"session_id": "smoke-001",
        "pesan": "Berapa pesangon kalau PHK?"}).json()["kategori_eskalasi"] == "phk_pesangon"
    assert c.get("/api/hrd/drafts").status_code == 401
    assert len(c.get("/api/hrd/drafts", headers=H).json()["drafts"]) == 1
    print("healthz, chat, pagar pengaman, gerbang token, draf: OK")
PY

echo
echo "SEMUA LOLOS"
