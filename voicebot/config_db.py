# -*- coding: utf-8 -*-
"""voicebot/config_db.py -- penyimpanan konfigurasi, intent, kamus, & log turn.

SQLite tunggal (env VOICEBOT_DB_FILE, default 'voicebot.db'). Tabel:
  vb_settings(key, value)                 -- konfigurasi mesin (ambang, prompt, dst.)
  vb_intents(id, name, phrases, response) -- intent + training phrase (NLU lokal)
  vb_lexicon(id, pattern, replacement...) -- kamus pelafalan (dipakai TTS)
  vb_turns(...)                           -- log tiap giliran

Gagal-anggun: fungsi baca mengembalikan default bila DB bermasalah.
"""
import os
import re
import json
import sqlite3

DB_FILE = os.environ.get("VOICEBOT_DB_FILE") or "voicebot.db"

DEFAULT_SETTINGS = {
    "threshold": "0.6",
    "stt_lang": "id",
    "stt_enabled": "1",
    "tts_enabled": "1",
    "llm_system": (
        "Anda adalah asisten suara call center berbahasa Indonesia. Jawab "
        "singkat, sopan, dan jelas untuk dibacakan. Bila tidak yakin, arahkan "
        "penelepon ke petugas."
    ),
    "handoff_triggers": (
        "bicara dengan agen, hubungkan ke petugas, mau bicara dengan orang, "
        "operator, customer service"
    ),
    "handoff_max_fallback": "2",
    "fallback_reply": (
        "Maaf, saya belum menangkap maksudnya. Boleh diulang dengan kalimat lain?"
    ),
    "rag_enabled": "1",
    "rag_top_k": "5",
    "pron_enabled": "1",
    "pron_spell_digits_min": "7",
    # --- dialog manager (#3) ---
    "dialog_enabled": "1",
    "confirm_min": "0.45",
    "salutation": "Bapak/Ibu",
    "salutation_enabled": "1",
    "greeting": (
        "Selamat datang di layanan kami. Ada yang bisa saya bantu, Bapak/Ibu?"
    ),
    "closing_reply": (
        "Baik, terima kasih sudah menghubungi kami. Semoga harinya menyenangkan."
    ),
    "handoff_reply": (
        "Baik, saya hubungkan Anda dengan agen kami. Mohon tunggu sebentar."
    ),
    "confirm_template": (
        "Mohon konfirmasi, apakah Anda menanyakan tentang {intent}?"
    ),
    "readback_template": "Saya ulangi ya, {text}. Apakah sudah benar?",
    "resume_template": (
        "Sebelumnya kita membahas {intent}. Mau lanjutkan itu setelah ini?"
    ),
    "resume_enabled": "0",
    "cmd_repeat": "ulangi, tolong ulangi, ulangi lagi, bisa diulang, ulang",
    "cmd_end": (
        "selesai, sudah cukup, cukup, tutup, sudah selesai, terima kasih sudah cukup"
    ),
    "affirmations": (
        "ya, iya, betul, benar, ya benar, betul sekali, benar sekali, oke, ok, "
        "iya betul, benar begitu"
    ),
    "negations": (
        "tidak, bukan, salah, tidak benar, bukan itu, nggak, enggak, gak, "
        "bukan begitu"
    ),
    "filler_enabled": "1",
    "filler_texts": (
        "Baik, mohon tunggu sebentar ya.|Baik, saya periksa dulu.|"
        "Mohon tunggu sebentar, ya."
    ),
    # --- penyingkat jawaban intent (2b) ---
    "intent_shorten_enabled": "0",
    "intent_shorten_min_chars": "160",
    "intent_shorten_system": (
        "Anda meringkas jawaban call center untuk dibacakan sebagai suara dalam "
        "Bahasa Indonesia. Persingkat menjadi 1-2 kalimat lisan yang sopan dan "
        "langsung ke inti, TANPA mengubah, menambah, atau menghilangkan fakta, "
        "angka, nominal, syarat, nama, atau langkah penting. Buang hanya kata "
        "berlebih dan pengulangan. Tanpa markdown, tanpa emoji. Bila teks sudah "
        "ringkas, kembalikan apa adanya."
    ),
    # --- mode suara natural (#4a) ---
    # tts_engine: 'piper' (default, ringan/cepat) | 'mms' (facebook/mms-tts-ind,
    # native Bahasa Indonesia, lebih natural). MMS butuh transformers+torch dan
    # unduhan model sekali dari HuggingFace; setelah itu jalan penuh lokal.
    "tts_engine": "piper",
    "mms_model": "facebook/mms-tts-ind",
}

_INIT_DONE = set()


def connect():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass
    return conn


def init_db(conn, force=False):
    key = None
    try:
        r = conn.execute("PRAGMA database_list").fetchone()
        key = r[2] if r else None
    except Exception:
        key = None
    if not force and key and key in _INIT_DONE:
        return conn
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS vb_settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS vb_intents (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            phrases    TEXT,
            response   TEXT,
            aktif      INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_vb_intent_name ON vb_intents(name);
        CREATE TABLE IF NOT EXISTS vb_lexicon (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern     TEXT NOT NULL,
            replacement TEXT,
            mode        TEXT DEFAULT 'eja',
            enabled     INTEGER DEFAULT 1,
            notes       TEXT,
            created_at  TEXT DEFAULT (datetime('now')),
            updated_at  TEXT DEFAULT (datetime('now'))
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_vb_lexicon_pat ON vb_lexicon(pattern);
        CREATE TABLE IF NOT EXISTS vb_turns (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            id_trace   TEXT,
            user_text  TEXT,
            intent     TEXT,
            confidence REAL,
            sumber     TEXT,
            bot_text   TEXT,
            handoff    INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_vb_turns_sess ON vb_turns(session_id);
        """
    )
    conn.commit()
    for k, v in DEFAULT_SETTINGS.items():
        try:
            conn.execute(
                "INSERT OR IGNORE INTO vb_settings(key, value) VALUES (?, ?)",
                (k, v),
            )
        except Exception:
            pass
    conn.commit()
    if key:
        _INIT_DONE.add(key)
    try:
        seed_intents(conn)
    except Exception:
        pass
    try:
        seed_lexicon(conn)
    except Exception:
        pass
    return conn


# ------------------------------------------------------------------ settings
def get_settings(conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        out = dict(DEFAULT_SETTINGS)
        for r in conn.execute("SELECT key, value FROM vb_settings").fetchall():
            out[r["key"]] = r["value"]
        return out
    finally:
        if own:
            conn.close()


def get_setting(key, default=None, conn=None):
    s = get_settings(conn)
    return s.get(key, default if default is not None else DEFAULT_SETTINGS.get(key))


def set_settings(data, conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        n = 0
        for k, v in (data or {}).items():
            conn.execute(
                "INSERT INTO vb_settings(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(k), "" if v is None else str(v)),
            )
            n += 1
        conn.commit()
        return {"saved": n}
    finally:
        if own:
            conn.close()


# ------------------------------------------------------------------ intents
def _phr_list(v):
    try:
        x = json.loads(v) if v else []
        return [str(t).strip() for t in x if str(t).strip()] if isinstance(x, list) else []
    except Exception:
        return []


def _norm_phrases(v):
    if isinstance(v, list):
        arr = [str(t).strip() for t in v if str(t).strip()]
    else:
        arr = [t.strip() for t in re.split(r"[,\n;]+", str(v or "")) if t.strip()]
    out, low = [], set()
    for t in arr:
        if t.lower() not in low:
            low.add(t.lower())
            out.append(t)
    return out


def list_intents(q="", conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        q = (q or "").strip()
        if q:
            rows = conn.execute(
                "SELECT * FROM vb_intents WHERE name LIKE ? OR COALESCE(phrases,'') LIKE ? "
                "ORDER BY name",
                ("%" + q + "%", "%" + q + "%"),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM vb_intents ORDER BY name").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["phrases_list"] = _phr_list(d.get("phrases"))
            out.append(d)
        return out
    finally:
        if own:
            conn.close()


def upsert_intent(data, conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        name = str(data.get("name") or "").strip()
        if not name:
            raise ValueError("field 'name' wajib diisi")
        phrases = json.dumps(_norm_phrases(data.get("phrases")), ensure_ascii=False)
        response = str(data.get("response") or "").strip()
        aktif = 0 if str(data.get("aktif")) in ("0", "false", "False", "no") else 1
        idv = data.get("id")
        if idv:
            conn.execute(
                "UPDATE vb_intents SET name=?, phrases=?, response=?, aktif=?, "
                "updated_at=datetime('now') WHERE id=?",
                (name, phrases, response, aktif, int(idv)),
            )
            new_id = int(idv)
        else:
            cur = conn.execute(
                "INSERT INTO vb_intents(name, phrases, response, aktif) VALUES (?,?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET phrases=excluded.phrases, "
                "response=excluded.response, aktif=excluded.aktif, updated_at=datetime('now')",
                (name, phrases, response, aktif),
            )
            new_id = int(cur.lastrowid or 0)
        conn.commit()
        return {"id": new_id}
    finally:
        if own:
            conn.close()


def delete_intent(id_, conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        conn.execute("DELETE FROM vb_intents WHERE id=?", (int(id_),))
        conn.commit()
        return {"dihapus": 1}
    finally:
        if own:
            conn.close()


def all_phrases(conn=None):
    """[(intent_name, phrase, response)] utk intent aktif -> dipakai NLU."""
    own = conn is None
    conn = conn or init_db(connect())
    try:
        rows = conn.execute(
            "SELECT name, phrases, response FROM vb_intents WHERE COALESCE(aktif,1)=1"
        ).fetchall()
        out = []
        for r in rows:
            resp = r["response"] or ""
            for ph in _phr_list(r["phrases"]):
                out.append((r["name"], ph, resp))
        return out
    finally:
        if own:
            conn.close()


def intent_response(name, conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        r = conn.execute("SELECT response FROM vb_intents WHERE name=?", (name,)).fetchone()
        return (r["response"] if r else "") or ""
    finally:
        if own:
            conn.close()


# ------------------------------------------------------------------ lexicon
def list_lexicon(q="", conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        q = (q or "").strip()
        if q:
            rows = conn.execute(
                "SELECT * FROM vb_lexicon WHERE pattern LIKE ? OR COALESCE(replacement,'') LIKE ? "
                "ORDER BY pattern",
                ("%" + q + "%", "%" + q + "%"),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM vb_lexicon ORDER BY pattern").fetchall()
        return [dict(r) for r in rows]
    finally:
        if own:
            conn.close()


def upsert_lexicon(data, conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        pattern = str(data.get("pattern") or "").strip()
        if not pattern:
            raise ValueError("field 'pattern' wajib diisi")
        replacement = str(data.get("replacement") or "").strip()
        mode = (str(data.get("mode") or "eja").strip() or "eja")
        enabled = 0 if str(data.get("enabled")) in ("0", "false", "False", "no") else 1
        notes = str(data.get("notes") or "").strip()
        idv = data.get("id")
        if idv:
            conn.execute(
                "UPDATE vb_lexicon SET pattern=?, replacement=?, mode=?, enabled=?, "
                "notes=?, updated_at=datetime('now') WHERE id=?",
                (pattern, replacement, mode, enabled, notes, int(idv)),
            )
            new_id = int(idv)
        else:
            cur = conn.execute(
                "INSERT INTO vb_lexicon(pattern, replacement, mode, enabled, notes) "
                "VALUES (?,?,?,?,?) ON CONFLICT(pattern) DO UPDATE SET "
                "replacement=excluded.replacement, mode=excluded.mode, "
                "enabled=excluded.enabled, notes=excluded.notes, updated_at=datetime('now')",
                (pattern, replacement, mode, enabled, notes),
            )
            new_id = int(cur.lastrowid or 0)
        conn.commit()
        return {"id": new_id}
    finally:
        if own:
            conn.close()


def delete_lexicon(id_, conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        conn.execute("DELETE FROM vb_lexicon WHERE id=?", (int(id_),))
        conn.commit()
        return {"dihapus": 1}
    finally:
        if own:
            conn.close()


def lexicon_map(conn=None):
    """[{pattern, replacement, mode}] utk entri aktif -> dipakai voicebot.pron."""
    own = conn is None
    conn = conn or init_db(connect())
    try:
        rows = conn.execute(
            "SELECT pattern, replacement, mode FROM vb_lexicon WHERE COALESCE(enabled,1)=1"
        ).fetchall()
        return [{"pattern": r["pattern"], "replacement": r["replacement"] or "",
                 "mode": r["mode"]} for r in rows]
    finally:
        if own:
            conn.close()


# ------------------------------------------------------------------ turns/log
def log_turn(rec, conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        conn.execute(
            "INSERT INTO vb_turns(session_id,id_trace,user_text,intent,confidence,"
            "sumber,bot_text,handoff) VALUES (?,?,?,?,?,?,?,?)",
            (
                rec.get("session_id"),
                rec.get("id_trace"),
                rec.get("user_text"),
                rec.get("intent"),
                float(rec.get("confidence") or 0.0),
                rec.get("sumber"),
                rec.get("bot_text"),
                1 if rec.get("handoff") else 0,
            ),
        )
        conn.commit()
    finally:
        if own:
            conn.close()


def list_turns(limit=50, conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        rows = conn.execute(
            "SELECT * FROM vb_turns ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if own:
            conn.close()


# ------------------------------------------------------------------ seed
_SEED_INTENTS = [
    {
        "name": "Cek Status NPWP",
        "phrases": ["cek status npwp", "status npwp saya", "npwp saya aktif atau tidak",
                    "mau tanya npwp", "cek npwp"],
        "response": ("Untuk pengecekan status NPWP, mohon sebutkan nomor NPWP Anda, "
                     "nanti saya bantu arahkan verifikasinya."),
    },
    {
        "name": "Jam Operasional",
        "phrases": ["jam buka", "jam operasional", "buka jam berapa", "kapan buka",
                    "jam layanan"],
        "response": ("Layanan kami buka Senin sampai Jumat, pukul 08.00 sampai 16.00 "
                     "waktu setempat."),
    },
]

_SEED_LEXICON = [
    ("NPWP", "en pe we pe", "eja"),
    ("NIK", "en i ka", "eja"),
    ("EFIN", "efin", "baca"),
    ("SPT", "es pe te", "eja"),
    ("PPh", "pe pe ha", "eja"),
    ("PPN", "pe pe en", "eja"),
    ("DJP", "de je pe", "eja"),
    ("KPP", "ka pe pe", "eja"),
    ("KTP", "ka te pe", "eja"),
    ("e-Filing", "i failing", "baca"),
    ("e-Billing", "i biling", "baca"),
    ("e-Faktur", "i faktur", "baca"),
]


def seed_intents(conn):
    try:
        n = conn.execute("SELECT COUNT(*) FROM vb_intents").fetchone()[0] or 0
    except Exception:
        return
    if n:
        return
    for it in _SEED_INTENTS:
        try:
            upsert_intent(it, conn=conn)
        except Exception:
            pass


def seed_lexicon(conn):
    try:
        n = conn.execute("SELECT COUNT(*) FROM vb_lexicon").fetchone()[0] or 0
    except Exception:
        return
    if n:
        return
    for pat, rep, mode in _SEED_LEXICON:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO vb_lexicon(pattern, replacement, mode, enabled) "
                "VALUES (?,?,?,1)",
                (pat, rep, mode),
            )
        except Exception:
            pass
    conn.commit()
