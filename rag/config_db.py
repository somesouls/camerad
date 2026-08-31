# -*- coding: utf-8 -*-
"""rag_config_db.py — Profil & konfigurasi mesin RAG (disimpan permanen).

Tiap \"profil\" mewakili satu peran AI yang berbagi backend LLM sama namun beda
persona/prompt:
  - \"chatbot\" : ChatBot Pajak untuk Wajib Pajak (ringkas, sumber disembunyikan)
  - \"agent\"   : Asisten Agent Kring Pajak (detail + tampilkan sumber & link)
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
  mode          TEXT  mode mesin: ''(auto)|tanpa_llm|llm|full (untuk pengujian)
  updated_at    TEXT

v30: migrasi lunak tambahan — bila prompt profil chatbot ternyata PERSIS prompt
bawaan AGENT (salah-isi bawaan lama), dikembalikan ke prompt bawaan chatbot
(anti over-abstain). Prompt kustom admin tidak disentuh.

v33 (paritas prompt agent): prompt profil AGENT dinaikkan agar setara chatbot —
anti over-abstain untuk kueri singkat/informal, mencantumkan catatan status atau
peraturan pengubah (induk) sebagai caveat dasar hukum, serta larangan
menampilkan nomor/pasal yang tak hadir di konteks. Migrasi lunak: agent hanya
di-upgrade bila prompt tersimpan masih PERSIS bawaan lama (belum dikustom admin).

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

# Mode mesin per-profil (dibaca rag_engine.answer):
#   '' (auto)   -> ikut env (profil cepat)
#   'tanpa_llm' -> tanpa LLM generatif (jalur cepat intent + cuplikan retrieval)
#   'llm'       -> pakai LLM tapi hemat (tanpa loop verifikasi & AI-rewrite)
#   'full'      -> pipeline penuh (AI-rewrite + loop verifikasi + sintesis)
_MODE_VALID = ("", "tanpa_llm", "llm", "full")

FALLBACK_DEFAULT = (
    "Mohon maaf, informasi mengenai hal tersebut belum tersedia pada basis "
    "data kami. Untuk memperoleh jawaban yang lebih pasti, Anda dapat "
    "menghubungi Kring Pajak di 1500200 atau mengunjungi kantor pajak "
    "terdekat. Terima kasih."
)

# Versi LAMA prompt chatbot (dipakai untuk migrasi lunak: hanya di-upgrade bila
# prompt tersimpan di DB masih PERSIS sama dengan ini alias belum dikustom admin).
_PROMPT_CHATBOT_LAMA = (
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
    "CARA MENJAWAB\n"
    "- Bila konteks memuat informasi yang relevan (meski hanya SEBAGIAN), "
    "JAWAB dari bagian yang relevan itu. Jangan menolak menjawab hanya karena "
    "konteks terasa kurang lengkap.\n"
    "- Bila hanya sebagian informasi tersedia, sampaikan dulu yang ada, lalu "
    "arahkan Wajib Pajak menghubungi Kring Pajak di 1500200 atau kantor pajak "
    "terdekat untuk rincian yang belum tercakup.\n"
    "- Gunakan HANYA kalimat fallback PERSIS berikut bila konteks benar-benar "
    "TIDAK memuat informasi apa pun yang berkaitan dengan pertanyaan: "
    "\"{{fallback}}\"\n\n"
    "GAYA\n"
    "Ringkas, jelas, dan langkah demi langkah bila prosedural. Jangan "
    "menampilkan nama sumber internal kepada pengguna."
)

# Versi LAMA prompt agent (dipakai untuk migrasi lunak: hanya di-upgrade bila
# prompt tersimpan di DB masih PERSIS sama dengan ini alias belum dikustom admin).
_PROMPT_AGENT_LAMA = (
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
    "- Bila konteks memuat informasi yang relevan (meski hanya SEBAGIAN), "
    "JAWAB dari bagian yang relevan itu. Jangan menolak menjawab hanya karena "
    "konteks terasa kurang lengkap atau pertanyaan singkat/informal.\n"
    "- Berikan jawaban runut. Bila konteks memuat catatan status peraturan "
    "(mis. peraturan telah diubah/dicabut, atau ada peraturan pengubah maupun "
    "induk pengubahnya), cantumkan pula catatan itu pada bagian dasar hukum "
    "sebagai caveat agar petugas memverifikasi versi mutakhir.\n"
    "- Jangan menampilkan nomor atau pasal peraturan yang TIDAK hadir di "
    "konteks. Kutip hanya rujukan yang benar-benar ada pada \"KONTEKS "
    "INTERNAL\".\n"
    "- Cantumkan rujukan pada bagian akhir:\n"
    "{{sumber}}\n\n"
    "BILA TIDAK ADA DI DATA\n"
    "Gunakan kalimat fallback PERSIS berikut HANYA bila konteks benar-benar "
    "TIDAK memuat informasi apa pun yang berkaitan dengan pertanyaan: "
    "\"{{fallback}}\""
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
    },
    "agent": {
        "nama": "Asisten Agent Kring Pajak",
        "system_prompt": _PROMPT_AGENT,
        "sumber": ["sop", "sosmed", "awe", "intent", "peraturan"],
        "maks_loop": 2,
        "tampil_sumber": 1,
        "fallback": FALLBACK_DEFAULT,
        "suhu": 0.3,
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
        "fallback TEXT, suhu REAL DEFAULT 0.3, mode TEXT DEFAULT '', updated_at TEXT)"
    )
    # Migrasi lunak: tambah kolom 'mode' bila DB lama belum memilikinya.
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(rag_profile)").fetchall()]
        if "mode" not in cols:
            conn.execute("ALTER TABLE rag_profile ADD COLUMN mode TEXT DEFAULT ''")
    except Exception:
        pass
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    for pid, d in _DEFAULTS.items():
        row = conn.execute("SELECT 1 FROM rag_profile WHERE id=?", (pid,)).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO rag_profile (id,nama,system_prompt,sumber,maks_loop,"
                "tampil_sumber,fallback,suhu,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (pid, d["nama"], d["system_prompt"], json.dumps(d["sumber"]),
                 d["maks_loop"], d["tampil_sumber"], d["fallback"], d["suhu"], now),
            )
    # Migrasi lunak prompt chatbot: bila prompt tersimpan masih versi LAMA (stok),
    # naikkan otomatis ke versi anti over-abstain. Prompt yang sudah dikustom
    # admin TIDAK diubah.
    try:
        r = conn.execute("SELECT system_prompt FROM rag_profile WHERE id='chatbot'").fetchone()
        if r and (r[0] or "").strip() == _PROMPT_CHATBOT_LAMA.strip():
            conn.execute(
                "UPDATE rag_profile SET system_prompt=?, updated_at=? WHERE id='chatbot'",
                (_PROMPT_CHATBOT, now),
            )
    except Exception:
        pass
    # Migrasi lunak v30: bila prompt profil chatbot ternyata PERSIS prompt bawaan
    # AGENT (salah-isi bawaan lama — teramati 19 Agu 2026: uji profil chatbot
    # memakai persona \"asisten untuk petugas\"), kembalikan ke prompt bawaan
    # chatbot. Prompt kustom admin TIDAK diubah.
    try:
        r = conn.execute("SELECT system_prompt FROM rag_profile WHERE id='chatbot'").fetchone()
        if r and (r[0] or "").strip() == _PROMPT_AGENT_LAMA.strip():
            conn.execute(
                "UPDATE rag_profile SET system_prompt=?, updated_at=? WHERE id='chatbot'",
                (_PROMPT_CHATBOT, now),
            )
    except Exception:
        pass
    # Migrasi lunak v33 (paritas prompt agent): bila prompt profil agent masih
    # PERSIS bawaan LAMA (belum dikustom admin), naikkan ke prompt agent baru
    # (anti over-abstain kueri singkat/informal + caveat induk pengubah/status
    # peraturan + larangan pasal di luar konteks). Prompt kustom admin TIDAK
    # diubah.
    try:
        r = conn.execute("SELECT system_prompt FROM rag_profile WHERE id='agent'").fetchone()
        if r and (r[0] or "").strip() == _PROMPT_AGENT_LAMA.strip():
            conn.execute(
                "UPDATE rag_profile SET system_prompt=?, updated_at=? WHERE id='agent'",
                (_PROMPT_AGENT, now),
            )
    except Exception:
        pass
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
    m = (d.get("mode") or "").strip().lower()
    d["mode"] = m if m in _MODE_VALID else ""
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
        mode = (data.get("mode") if data.get("mode") is not None else cur.get("mode")) or ""
        mode = str(mode).strip().lower()
        if mode not in _MODE_VALID:
            mode = ""
        conn.execute(
            "INSERT INTO rag_profile (id,nama,system_prompt,sumber,maks_loop,"
            "tampil_sumber,fallback,suhu,mode,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET nama=excluded.nama,"
            "system_prompt=excluded.system_prompt,sumber=excluded.sumber,"
            "maks_loop=excluded.maks_loop,tampil_sumber=excluded.tampil_sumber,"
            "fallback=excluded.fallback,suhu=excluded.suhu,mode=excluded.mode,"
            "updated_at=excluded.updated_at",
            (pid, nama, prompt, json.dumps(sumber), maks_loop, tampil,
             fallback, suhu, mode, time.strftime("%Y-%m-%d %H:%M:%S")),
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
