# -*- coding: utf-8 -*-
"""rag_config_db.py — Profil & konfigurasi mesin RAG (disimpan permanen).

Tiap "profil" mewakili satu peran AI yang berbagi backend LLM sama namun beda
persona/prompt:
  - "chatbot" : ChatBot Pajak untuk Wajib Pajak (ringkas, sumber disembunyikan)
  - "agent"   : Asisten Agent Kring Pajak (detail + tampilkan sumber & link)
  - (voicebot menyusul; cukup tambah profil baru saat waktunya)

Kolom profil:
  id            TEXT  (chatbot|agent|...)
  nama          TEXT
  system_prompt TEXT  (boleh memuat chip sumber @intent/@awe/@sosmed/@peraturan/
                       @sop dan placeholder {{konteks}}/{{sumber}}/{{fallback}})
  sumber        TEXT  JSON list sumber aktif (subset dari SUMBER_VALID)
  maks_loop     INT   batas putaran verifikasi (default 2)
  tampil_sumber INT   1=tampilkan daftar sumber ke pengguna
  fallback      TEXT  kalimat baku bila tak ada konteks relevan
  suhu          REAL  temperature LLM
  model         TEXT  (opsional) override model/deployment LLM utk profil ini;
                       kosong = pakai model global dari .env
  updated_at    TEXT

Env: PIPELINE_RAG_DB_FILE atau 'rag.db'.
"""
import os
import json
import sqlite3
import time

DB_FILE = os.environ.get("PIPELINE_RAG_DB_FILE") or "rag.db"

SUMBER_VALID = ("intent", "awe", "sosmed", "peraturan", "sop")
SUMBER_LABEL = {
    "intent": "Training Phrase & Intent",
    "awe": "Percakapan AWE",
    "sosmed": "Data Sosmed",
    "peraturan": "Peraturan",
    "sop": "SOP & Proses Bisnis",
}

FALLBACK_DEFAULT = (
    "Mohon maaf, informasi mengenai hal tersebut belum tersedia pada basis "
    "data kami. Untuk memperoleh jawaban yang lebih pasti, Anda dapat "
    "menghubungi Kring Pajak di 1500200 atau mengunjungi kantor pajak "
    "terdekat. Terima kasih."
)

_PROMPT_CHATBOT = (
    "PERAN\n"
    "Kamu adalah Agent Kring Pajak - asisten informasi layanan perpajakan "
    "resmi untuk Wajib Pajak. Jawab dengan bahasa Indonesia yang ramah, "
    "formal, sopan, dan normatif.\n\n"
    "SUMBER JAWABAN\n"
    "Gunakan HANYA \"KONTEKS INTERNAL\" di bawah. Untuk pertanyaan seputar "
    "aplikasi/prosedur, utamakan @sop @sosmed @awe. Untuk dasar hukum/"
    "ketentuan, gunakan @peraturan. Dilarang memakai pengetahuan umum atau "
    "sumber web, dan dilarang mengarang fakta, angka, tautan, atau prosedur.\n\n"
    "{{konteks}}\n\n"
    "BILA TIDAK ADA DI DATA\n"
    "Jika konteks tidak memuat informasi relevan, balas PERSIS: \"{{fallback}}\"\n\n"
    "GAYA\n"
    "Ringkas, jelas, dan langkah demi langkah bila prosedural. Jangan "
    "menampilkan nama sumber internal kepada pengguna."
)

_PROMPT_AGENT = (
    "PERAN\n"
    "Kamu adalah asisten untuk petugas Agent Kring Pajak. Bantu petugas "
    "menemukan jawaban yang akurat beserta rujukannya. Bahasa Indonesia "
    "formal dan lugas.\n\n"
    "SUMBER JAWABAN\n"
    "Gunakan HANYA \"KONTEKS INTERNAL\". Petakan: pertanyaan aplikasi/prosedur "
    "-> @sop @sosmed @awe; dasar hukum -> @peraturan; maksud/intent -> @intent. "
    "Dilarang memakai pengetahuan umum/web atau mengarang.\n\n"
    "{{konteks}}\n\n"
    "CARA MENJAWAB\n"
    "Berikan jawaban runut, lalu cantumkan rujukan pada bagian akhir:\n"
    "{{sumber}}\n\n"
    "BILA TIDAK ADA DI DATA\n"
    "Jika konteks tidak memuat informasi relevan, balas PERSIS: \"{{fallback}}\""
)

_DEFAULTS = {
    "chatbot": {
        "nama": "ChatBot Pajak (Wajib Pajak)",
        "system_prompt": _PROMPT_CHATBOT,
        "sumber": ["sop", "sosmed", "awe", "intent", "peraturan"],
        "maks_loop": 2,
        "tampil_sumber": 0,
        "fallback": FALLBACK_DEFAULT,
        "suhu": 0.3,
        "model": "",
    },
    "agent": {
        "nama": "Asisten Agent Kring Pajak",
        "system_prompt": _PROMPT_AGENT,
        "sumber": ["sop", "sosmed", "awe", "intent", "peraturan"],
        "maks_loop": 2,
        "tampil_sumber": 1,
        "fallback": FALLBACK_DEFAULT,
        "suhu": 0.3,
        "model": "",
    },
}


def connect():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass
    return conn


def init_db(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS rag_profile ("
        "id TEXT PRIMARY KEY, nama TEXT, system_prompt TEXT, sumber TEXT, "
        "maks_loop INTEGER DEFAULT 2, tampil_sumber INTEGER DEFAULT 0, "
        "fallback TEXT, suhu REAL DEFAULT 0.3, model TEXT, updated_at TEXT)"
    )
    # Migrasi lembut: tambah kolom 'model' bila DB lama belum memilikinya.
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(rag_profile)").fetchall()]
        if "model" not in cols:
            conn.execute("ALTER TABLE rag_profile ADD COLUMN model TEXT")
    except Exception:
        pass
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    for pid, d in _DEFAULTS.items():
        row = conn.execute("SELECT 1 FROM rag_profile WHERE id=?", (pid,)).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO rag_profile (id,nama,system_prompt,sumber,maks_loop,"
                "tampil_sumber,fallback,suhu,model,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (pid, d["nama"], d["system_prompt"], json.dumps(d["sumber"]),
                 d["maks_loop"], d["tampil_sumber"], d["fallback"], d["suhu"],
                 d.get("model", ""), now),
            )
    conn.commit()
    return conn


def _row_to_dict(r):
    d = dict(r)
    try:
        d["sumber"] = json.loads(d.get("sumber") or "[]")
    except Exception:
        d["sumber"] = []
    d["sumber"] = [s for s in d["sumber"] if s in SUMBER_VALID]
    d["tampil_sumber"] = bool(d.get("tampil_sumber"))
    try:
        d["maks_loop"] = int(d.get("maks_loop") or 2)
    except Exception:
        d["maks_loop"] = 2
    try:
        d["suhu"] = float(d.get("suhu") if d.get("suhu") is not None else 0.3)
    except Exception:
        d["suhu"] = 0.3
    d["model"] = str(d.get("model") or "").strip()
    return d


def get_profile(pid):
    conn = init_db(connect())
    try:
        r = conn.execute("SELECT * FROM rag_profile WHERE id=?", (pid,)).fetchone()
        return _row_to_dict(r) if r else None
    finally:
        conn.close()


def list_profiles():
    conn = init_db(connect())
    try:
        rows = conn.execute("SELECT * FROM rag_profile ORDER BY id").fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def save_profile(pid, data):
    pid = (pid or "").strip()
    if not pid:
        raise ValueError("id profil wajib")
    conn = init_db(connect())
    try:
        cur = get_profile(pid) or dict(_DEFAULTS.get(pid, _DEFAULTS["chatbot"]))
        nama = (data.get("nama") or cur.get("nama") or pid).strip()
        prompt = data.get("system_prompt")
        prompt = prompt if prompt is not None else cur.get("system_prompt")
        sumber = data.get("sumber")
        if sumber is None:
            sumber = cur.get("sumber") or []
        sumber = [s for s in sumber if s in SUMBER_VALID]
        maks_loop = int(data.get("maks_loop", cur.get("maks_loop", 2)) or 2)
        maks_loop = max(0, min(maks_loop, 4))
        tampil = 1 if data.get("tampil_sumber", cur.get("tampil_sumber")) else 0
        fallback = data.get("fallback") or cur.get("fallback") or FALLBACK_DEFAULT
        suhu = float(data.get("suhu", cur.get("suhu", 0.3)) or 0.3)
        model_v = data.get("model")
        if model_v is None:
            model_v = cur.get("model")
        model_v = str(model_v or "").strip()
        conn.execute(
            "INSERT INTO rag_profile (id,nama,system_prompt,sumber,maks_loop,"
            "tampil_sumber,fallback,suhu,model,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET nama=excluded.nama,"
            "system_prompt=excluded.system_prompt,sumber=excluded.sumber,"
            "maks_loop=excluded.maks_loop,tampil_sumber=excluded.tampil_sumber,"
            "fallback=excluded.fallback,suhu=excluded.suhu,model=excluded.model,"
            "updated_at=excluded.updated_at",
            (pid, nama, prompt, json.dumps(sumber), maks_loop, tampil,
             fallback, suhu, model_v, time.strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
        return get_profile(pid)
    finally:
        conn.close()


def chips_in_prompt(prompt_text):
    """Kembalikan daftar sumber yang disebut lewat chip @sumber dalam prompt."""
    if not prompt_text:
        return []
    low = prompt_text.lower()
    return [s for s in SUMBER_VALID if ("@" + s) in low]
