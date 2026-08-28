# -*- coding: utf-8 -*-
"""avaya/phone_db.py - simpan interaksi Telepon (tabel awe_phone_interactions,
terpisah dari awe_conversations Chat). File DB sama dgn db.py (AVAYA_DB_FILE)."""
import json as _json

try:
    from .db import connect, _jkt_now_iso
except Exception:
    from db import connect, _jkt_now_iso

_DDL = """
CREATE TABLE IF NOT EXISTS awe_phone_interactions (
    sid TEXT PRIMARY KEY, day TEXT, tanggal TEXT, ani TEXT, dnis TEXT,
    call_id TEXT, site_id TEXT, durasi INTEGER DEFAULT 0, hold_time_sec INTEGER,
    has_audio INTEGER DEFAULT 0, has_screen INTEGER DEFAULT 0, audio_ref TEXT,
    customer TEXT, agent_name TEXT, transkrip_source TEXT, stt_model TEXT,
    stt_chunks INTEGER, stt_elapsed REAL, stt_text TEXT, transkrip_json TEXT,
    ringkasan TEXT, topik TEXT, jenis_layanan TEXT, sentiment TEXT, emotion TEXT,
    resolusi TEXT, frustrasi TEXT, entitas_json TEXT, poin_json TEXT,
    analisis_json TEXT, pulled_by TEXT, pulled_at TEXT, analyzed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_phone_day ON awe_phone_interactions(day);
CREATE INDEX IF NOT EXISTS idx_phone_tgl ON awe_phone_interactions(tanggal);
"""

_INITED = set()


def init_phone_db(conn):
    if id(conn) in _INITED:
        return conn
    conn.executescript(_DDL)
    conn.commit()
    _INITED.add(id(conn))
    return conn


def _dumps_opt(v):
    if v in (None, "", [], {}):
        return None
    return _json.dumps(v, ensure_ascii=False)


def _to_text(v):
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        return _json.dumps(v, ensure_ascii=False)
    return str(v)


def _entitas_nama(a):
    e = (a or {}).get("entitas")
    return ((e.get("nama") or "").strip() or None) if isinstance(e, dict) else None


def _norm_dialog(dialog):
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
        conn.execute("INSERT OR IGNORE INTO awe_phone_interactions(sid) VALUES(?)", (sid,))
        return True
    cols = ["sid"] + list(fields.keys())
    ph = ",".join("?" for _ in cols)
    setc = ",".join(k + "=excluded." + k for k in fields.keys())
    conn.execute("INSERT INTO awe_phone_interactions(" + ",".join(cols) +
                 ") VALUES(" + ph + ") ON CONFLICT(sid) DO UPDATE SET " + setc,
                 [sid] + list(fields.values()))
    return True


def stage_phone_pull(conn, day, rows, pulled_by=None):
    """Tahap 1: simpan metadata + audio hasil tarik (belum transkrip)."""
    init_phone_db(conn)
    now = _jkt_now_iso()
    n = 0
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        sid = str(r.get("sid") or "").strip()
        if not sid:
            continue
        f = {
            "day": (str(r.get("day") or day or "")[:10]) or None,
            "tanggal": r.get("tanggal"), "ani": r.get("ani"),
            "dnis": r.get("dnis"), "call_id": r.get("call_id"),
            "site_id": r.get("site_id"), "durasi": r.get("durasi"),
            "hold_time_sec": r.get("hold_time_sec"),
            "has_audio": 1 if (r.get("audio_ref") or r.get("has_audio")) else 0,
            "has_screen": r.get("has_screen"), "audio_ref": r.get("audio_ref"),
            "customer": r.get("customer"), "agent_name": r.get("agent_name"),
            "pulled_by": pulled_by, "pulled_at": now,
        }
        if _upsert(conn, sid, f):
            n += 1
    conn.commit()
    return {"staged": n}


def save_phone_analysis(conn, sid, transkrip=None, transkrip_source=None,
                        stt=None, analisis=None, customer=None):
    """Tahap 2: isi transkrip (STT) + hasil analisis (LLM) per interaksi."""
    init_phone_db(conn)
    stt = stt or {}
    analisis = analisis or {}
    f = {
        "transkrip_source": transkrip_source or ("qwen3-asr" if stt.get("model") else None),
        "stt_model": stt.get("model"), "stt_chunks": stt.get("chunks"),
        "stt_elapsed": stt.get("elapsed"), "stt_text": stt.get("text"),
        "transkrip_json": _dumps_opt(_norm_dialog(transkrip)),
    }
    if analisis:
        f.update({
            "ringkasan": analisis.get("ringkasan"), "topik": analisis.get("topik"),
            "jenis_layanan": analisis.get("jenis_layanan"),
            "sentiment": analisis.get("sentimen") or analisis.get("sentiment"),
            "emotion": analisis.get("emosi") or analisis.get("emotion"),
            "resolusi": analisis.get("resolusi"),
            "frustrasi": _to_text(analisis.get("frustrasi")),
            "entitas_json": _dumps_opt(analisis.get("entitas")),
            "poin_json": _dumps_opt(analisis.get("poin_penting")),
            "analisis_json": _json.dumps(analisis, ensure_ascii=False),
            "analyzed_at": _jkt_now_iso(),
        })
    nm = (customer or "").strip() if customer else _entitas_nama(analisis)
    if nm:
        f["customer"] = nm
    _upsert(conn, sid, f)
    conn.commit()
    return {"sid": str(sid).strip(), "ok": True}
