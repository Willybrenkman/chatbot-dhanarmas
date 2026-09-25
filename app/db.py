"""Penyimpanan percakapan, draf, eskalasi, dan onboarding.

SQLite dipilih supaya pilot bisa jalan tanpa infrastruktur tambahan.
Skemanya sengaja normal dan eksplisit (sessions / messages / drafts /
escalations) supaya pindah ke Postgres nanti cukup mengganti driver.

Aturan penting yang ditegakkan di sini:
  - setiap percakapan tersimpan terstruktur sejak hari pertama, bukan log teks
  - onboarding_sends punya UNIQUE(enrollment_id, step_id) sebagai kunci
    idempotensi, supaya scheduler yang jalan dua kali tidak mengirim dobel
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id              TEXT PRIMARY KEY,
    employee_ref    TEXT,
    employee_status TEXT NOT NULL DEFAULT 'tidak_diketahui',
    channel         TEXT NOT NULL DEFAULT 'web',
    created_at      TEXT NOT NULL,
    last_active_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT NOT NULL REFERENCES sessions(id),
    turn_index    INTEGER NOT NULL,
    role          TEXT NOT NULL,
    content       TEXT NOT NULL,
    sources       TEXT,
    found_answer  INTEGER,
    provider      TEXT,
    model         TEXT,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    cached_tokens INTEGER,
    latency_ms    INTEGER,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, turn_index);

CREATE TABLE IF NOT EXISTS drafts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT NOT NULL REFERENCES sessions(id),
    message_id    INTEGER NOT NULL REFERENCES messages(id),
    question      TEXT NOT NULL,
    draft_answer  TEXT NOT NULL,
    final_answer  TEXT,
    status        TEXT NOT NULL DEFAULT 'pending',
    reviewer      TEXT,
    reject_reason TEXT,
    created_at    TEXT NOT NULL,
    reviewed_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_drafts_status ON drafts(status, created_at);

CREATE TABLE IF NOT EXISTS escalations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL REFERENCES sessions(id),
    question    TEXT NOT NULL,
    category    TEXT NOT NULL,
    urgency     TEXT NOT NULL DEFAULT 'normal',
    status      TEXT NOT NULL DEFAULT 'open',
    handled_by  TEXT,
    note        TEXT,
    created_at  TEXT NOT NULL,
    handled_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_escalations_status ON escalations(status, urgency, created_at);

CREATE TABLE IF NOT EXISTS onboarding_enrollments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_ref  TEXT NOT NULL UNIQUE,
    nama          TEXT NOT NULL,
    phone         TEXT,
    email         TEXT,
    divisi        TEXT,
    status_kerja  TEXT,
    tanggal_mulai TEXT NOT NULL,
    atasan_phone  TEXT,
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS onboarding_sends (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    enrollment_id INTEGER NOT NULL REFERENCES onboarding_enrollments(id),
    step_id       TEXT NOT NULL,
    scheduled_for TEXT NOT NULL,
    channel       TEXT,
    status        TEXT NOT NULL DEFAULT 'pending',
    error         TEXT,
    sent_at       TEXT,
    UNIQUE(enrollment_id, step_id)
);
CREATE INDEX IF NOT EXISTS idx_sends_pending ON onboarding_sends(status, scheduled_for);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    # ---------- sessions & messages ----------

    def ensure_session(
        self,
        session_id: str,
        *,
        employee_ref: str | None = None,
        employee_status: str = "tidak_diketahui",
        channel: str = "web",
    ) -> None:
        ts = now_iso()
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO sessions
                        (id, employee_ref, employee_status, channel, created_at, last_active_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                        last_active_at  = excluded.last_active_at,
                        employee_ref    = COALESCE(excluded.employee_ref, sessions.employee_ref),
                        employee_status = CASE
                            WHEN excluded.employee_status != 'tidak_diketahui'
                            THEN excluded.employee_status ELSE sessions.employee_status END""",
                (session_id, employee_ref, employee_status, channel, ts, ts),
            )

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return dict(row) if row else None

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        sources: list[str] | None = None,
        found_answer: bool | None = None,
        provider: str | None = None,
        model: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cached_tokens: int | None = None,
        latency_ms: int | None = None,
    ) -> int:
        with self.connect() as conn:
            turn = conn.execute(
                "SELECT COALESCE(MAX(turn_index), -1) + 1 AS t FROM messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()["t"]
            cur = conn.execute(
                """INSERT INTO messages
                       (session_id, turn_index, role, content, sources, found_answer,
                        provider, model, input_tokens, output_tokens, cached_tokens,
                        latency_ms, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    session_id, turn, role, content,
                    json.dumps(sources, ensure_ascii=False) if sources else None,
                    None if found_answer is None else int(found_answer),
                    provider, model, input_tokens, output_tokens, cached_tokens,
                    latency_ms, now_iso(),
                ),
            )
            return int(cur.lastrowid)

    def recent_messages(self, session_id: str, limit_turns: int) -> list[dict[str, Any]]:
        """Riwayat percakapan terbaru, urut lama -> baru. Hanya user & assistant."""
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT role, content FROM messages
                    WHERE session_id = ? AND role IN ('user', 'assistant')
                    ORDER BY turn_index DESC LIMIT ?""",
                (session_id, limit_turns * 2),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    # ---------- drafts ----------

    def add_draft(self, session_id: str, message_id: int, question: str, answer: str) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """INSERT INTO drafts (session_id, message_id, question, draft_answer, created_at)
                   VALUES (?,?,?,?,?)""",
                (session_id, message_id, question, answer, now_iso()),
            )
            return int(cur.lastrowid)

    def list_drafts(self, status: str = "pending", limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM drafts WHERE status = ? ORDER BY created_at ASC LIMIT ?",
                (status, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def review_draft(
        self, draft_id: int, *, status: str, reviewer: str,
        final_answer: str | None = None, reject_reason: str | None = None,
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            cur = conn.execute(
                """UPDATE drafts
                      SET status = ?, reviewer = ?, final_answer = ?, reject_reason = ?,
                          reviewed_at = ?
                    WHERE id = ? AND status = 'pending'""",
                (status, reviewer, final_answer, reject_reason, now_iso(), draft_id),
            )
            if cur.rowcount == 0:
                return None
            row = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        return dict(row) if row else None

    # ---------- escalations ----------

    def add_escalation(
        self, session_id: str, question: str, category: str, urgency: str = "normal"
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """INSERT INTO escalations (session_id, question, category, urgency, created_at)
                   VALUES (?,?,?,?,?)""",
                (session_id, question, category, urgency, now_iso()),
            )
            return int(cur.lastrowid)

    def list_escalations(self, status: str = "open", limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT * FROM escalations WHERE status = ?
                    ORDER BY CASE urgency WHEN 'kritis' THEN 0 WHEN 'tinggi' THEN 1 ELSE 2 END,
                             created_at ASC LIMIT ?""",
                (status, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def handle_escalation(self, esc_id: int, handled_by: str, note: str | None) -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                """UPDATE escalations SET status='handled', handled_by=?, note=?, handled_at=?
                    WHERE id=? AND status='open'""",
                (handled_by, note, now_iso(), esc_id),
            )
            return cur.rowcount > 0

    # ---------- statistik sederhana ----------

    def stats(self) -> dict[str, Any]:
        with self.connect() as conn:
            q = conn.execute
            total_q = q("SELECT COUNT(*) c FROM messages WHERE role='user'").fetchone()["c"]
            answered = q(
                "SELECT COUNT(*) c FROM messages WHERE role='assistant' AND found_answer=1"
            ).fetchone()["c"]
            not_found = q(
                "SELECT COUNT(*) c FROM messages WHERE role='assistant' AND found_answer=0"
            ).fetchone()["c"]
            sessions = q("SELECT COUNT(*) c FROM sessions").fetchone()["c"]
            esc = q(
                "SELECT category, COUNT(*) c FROM escalations GROUP BY category ORDER BY c DESC"
            ).fetchall()
            drafts = q(
                "SELECT status, COUNT(*) c FROM drafts GROUP BY status"
            ).fetchall()
            tok = q(
                """SELECT COALESCE(SUM(input_tokens),0) i, COALESCE(SUM(output_tokens),0) o,
                          COALESCE(SUM(cached_tokens),0) c FROM messages"""
            ).fetchone()
        jawab_rate = round(answered / total_q * 100, 1) if total_q else 0.0
        return {
            "total_sesi": sessions,
            "total_pertanyaan": total_q,
            "terjawab": answered,
            "tidak_ditemukan": not_found,
            "persen_terjawab": jawab_rate,
            "eskalasi_per_kategori": {r["category"]: r["c"] for r in esc},
            "draf_per_status": {r["status"]: r["c"] for r in drafts},
            "token": {"input": tok["i"], "output": tok["o"], "cache_read": tok["c"]},
        }

    # ---------- onboarding ----------

    def upsert_enrollment(self, data: dict[str, Any]) -> int:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO onboarding_enrollments
                       (employee_ref, nama, phone, email, divisi, status_kerja,
                        tanggal_mulai, atasan_phone, created_at)
                   VALUES (:employee_ref,:nama,:phone,:email,:divisi,:status_kerja,
                           :tanggal_mulai,:atasan_phone,:created_at)
                   ON CONFLICT(employee_ref) DO UPDATE SET
                        nama=excluded.nama, phone=excluded.phone, email=excluded.email,
                        divisi=excluded.divisi, status_kerja=excluded.status_kerja,
                        tanggal_mulai=excluded.tanggal_mulai,
                        atasan_phone=excluded.atasan_phone""",
                {**data, "created_at": now_iso()},
            )
            row = conn.execute(
                "SELECT id FROM onboarding_enrollments WHERE employee_ref = ?",
                (data["employee_ref"],),
            ).fetchone()
            return int(row["id"])

    def active_enrollments(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM onboarding_enrollments WHERE active = 1"
            ).fetchall()
        return [dict(r) for r in rows]

    def claim_send(self, enrollment_id: int, step_id: str, scheduled_for: str) -> bool:
        """Klaim satu langkah kirim. False kalau sudah pernah diklaim (idempoten)."""
        with self.connect() as conn:
            try:
                conn.execute(
                    """INSERT INTO onboarding_sends (enrollment_id, step_id, scheduled_for)
                       VALUES (?,?,?)""",
                    (enrollment_id, step_id, scheduled_for),
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def mark_send(
        self, enrollment_id: int, step_id: str, *, status: str,
        channel: str | None = None, error: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """UPDATE onboarding_sends
                      SET status=?, channel=?, error=?, sent_at=?
                    WHERE enrollment_id=? AND step_id=?""",
                (status, channel, error, now_iso(), enrollment_id, step_id),
            )

    def onboarding_report(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT e.nama, e.divisi, e.tanggal_mulai, s.step_id, s.status,
                          s.scheduled_for, s.channel, s.sent_at, s.error
                     FROM onboarding_sends s
                     JOIN onboarding_enrollments e ON e.id = s.enrollment_id
                    ORDER BY e.tanggal_mulai DESC, s.scheduled_for ASC"""
            ).fetchall()
        return [dict(r) for r in rows]
