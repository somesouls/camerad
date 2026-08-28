# -*- coding: utf-8 -*-
"""avaya/phone_db.py - penyimpanan interaksi Telepon (AWE Phone).

SENGAJA TERPISAH dari tabel awe_conversations (Chat) supaya query/analitik
Chat TIDAK terpengaruh sama sekali. Satu tabel khusus: awe_phone_interactions
(PK = sid). Memakai file DB + koneksi yang sama dengan avaya/db.py
(default avaya.db, override via env AVAYA_DB_FILE).

Alur dua tahap (mirror pola staging Chat):
  1) stage_phone_pull(conn, day, rows)   -> simpan metadata + audio hasil tarik
     (belum ada transkrip). Cepat, tanpa GPU.
  2) save_phone_analysis(conn, sid, ...)  -> isi transkrip (STT Qwen3-ASR) +
     hasil analisis (LLM phone_llm) per interaksi. Lambat (GPU + LLM).

Baca/tampil: list_phone(), get_phone_interaction(), phone_coverage(),
phone_stats(), delete_phone_day().

Hanya stdlib (sqlite3 via koneksi db.py + json).
"""
import json as _json

try:
    from .db import connect, _jkt_now_iso
except Exception:  # dijalankan sebagai skrip lepas
    from db import connect, _jkt_now_iso


_DDL = """
CREATE TABLE IF NOT EXISTS awe_phone_interactions (
    sid              TEXT PRIMARY KEY,
    day              TEXT,
    tanggal          TEXT,
    ani              TEXT,
    dnis             TEXT,
    call_id          TEXT,
    site_id          TEXT,
    durasi           INTEGER DEFAULT 0,
    hold_time_sec    INTEGER,
    has_audio        INTEGER DEFAULT 0,
    has_screen       INTEGER DEFAULT 0,
    audio_ref        TEXT,
    customer         TEXT,
    agent_name       TEXT,
    transkrip_source TEXT,
    stt_model        TEXT,
    stt_chunks       INTEGER,
    stt_elapsed      REAL,
    stt_text         TEXT,
    transkrip_json   TEXT,
    ringkasan        TEXT,
    topik            TEXT,
    jenis_layanan    TEXT,
    sentiment        TEXT,
    emotion          TEXT,
    resolusi         TEXT,
    frustrasi        TEXT,
    entitas_json     TEXT,
    poin_json        TEXT,
    analisis_json    TEXT,
    pulled_by        TEXT,
    pulled_at        TEXT,
    analyzed_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_phone_day ON awe_phone_interactions(day);
CREATE INDEX IF NOT EXISTS idx_phone_tgl ON awe_phone_interactions(tanggal);
"""

_INITED = set()


def init_phone_db(conn):
    """Buat tabel + index bila belum ada (idempoten, murah)."""
    key = id(conn)
    if key in _INITED:
        return conn
    conn.executescript(_DDL)
    conn.commit()
    _INITED.add(key)
    return conn


# --------------------------------------------------------------------------
# util
# --------------------------------------------------------------------------
def _to_text(v):
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        return _json.dumps(v, ensure_ascii=False)
    return str(v)


def _dumps_opt(v):
    if v in (None, "", [], {}):
        return None
    return _json.dumps(v, ensure_ascii=False)


def _entitas_nama(analisis):
    e = (analisis or {}).get("entitas")
    if isinstance(e, dict):
        return (e.get("nama") or "").strip() or None
    return None


def _norm_dialog(dialog):
    """Normalisasi [{penutur,teks}] / [{role,text}] -> [{role,text}]."""
    out = []
    for m in dialog or []:
        if isinstance(m, dict):
            role = m.get("role") or m.get("penutur") or m.get("speaker") or ""
            text = m.get("text") or m.get("teks") or m.get("isi") or ""
            if str(text).strip():
                out.append({"role": str(role), "text": str(text)})
        elif isinstance(m, str) and m.strip():
            out.append({"role": "", "text": m})
    return out or None


def _upsert(conn, sid, fields):
    sid = str(sid or "").strip()
    if not sid:
        return False
    fields = {k: v for k, v in (fields or {}).items() if v is not None}
    if not fields:
        conn.execute(
            "INSERT OR IGNORE INTO awe_phone_interactions(sid) VALUES(?)", (sid,))
        return True
    cols = ["sid"] + list(fields.keys())
    vals = [sid] + list(fields.values())
    ph = ",".join("?" for _ in cols)
    setc = ",".join(k + "=excluded." + k for k in fields.keys())
    conn.execute(
        "INSERT INTO awe_phone_interactions(" + ",".join(cols) + ") VALUES(" + ph + ") "
        "ON CONFLICT(sid) DO UPDATE SET " + setc, vals)
    return True


# --------------------------------------------------------------------------
# tulis
# --------------------------------------------------------------------------
def stage_phone_pull(conn, day, rows, pulled_by=None):