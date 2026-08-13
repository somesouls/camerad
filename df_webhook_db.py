# -*- coding: utf-8 -*-
"""df_webhook_db.py — Konfigurasi webhook Dialogflow ES (chatbot Kring Pajak).

Menyimpan pengaturan endpoint fulfillment Dialogflow ES secara permanen:
  - aktif        : 1/0 webhook diaktifkan
  - token        : rahasia yang WAJIB dikirim Dialogflow (header X-Camerad-Token
                   atau query ?token=) agar request diterima
  - profil       : profil mesin RAG yang dipakai ('chatbot'|'agent'|...)
  - deadline_ms  : batas waktu jawab sebelum fast-path mengirim fallback
                   (Dialogflow ES memutus webhook pada ~5 dtk; default 4500)
  - fallback     : kalimat balasan cepat bila jawaban belum siap saat deadline
  - pakai_riwayat: 1/0 pakai riwayat percakapan per-session (in-memory)
  - riwayat_turn : jumlah giliran terakhir yang diingat
  - updated_at

Env: PIPELINE_DF_WEBHOOK_DB_FILE atau 'df_webhook.db'.
"""
import os
import secrets
import sqlite3
import time

DB_FILE = os.environ.get("PIPELINE_DF_WEBHOOK_DB_FILE") or "df_webhook.db"

FALLBACK_DEFAULT = (
    "Mohon maaf, saat ini sistem sedang memproses pertanyaan Anda dan "
    "membutuhkan waktu sedikit lebih lama. Silakan ulangi pertanyaan Anda "
    "sebentar lagi, atau hubungi Kring Pajak di 1500200. Terima kasih."
)

_DEFAULTS = {
    "aktif": 1,
    "profil": "chatbot",
    "deadline_ms": 4500,
    "fallback": FALLBACK_DEFAULT,
    "pakai_riwayat": 1,
    "riwayat_turn": 3,
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
        "CREATE TABLE IF NOT EXISTS df_webhook_config ("
        "id INTEGER PRIMARY KEY CHECK (id=1), aktif INTEGER DEFAULT 1, "
        "token TEXT, profil TEXT DEFAULT 'chatbot', deadline_ms INTEGER DEFAULT 4500, "
        "fallback TEXT, pakai_riwayat INTEGER DEFAULT 1, riwayat_turn INTEGER DEFAULT 3, "
        "updated_at TEXT)"
    )
    row = conn.execute("SELECT id FROM df_webhook_config WHERE id=1").fetchone()
    if not row:
        conn.execute(
            "INSERT INTO df_webhook_config (id,aktif,token,profil,deadline_ms,"
            "fallback,pakai_riwayat,riwayat_turn,updated_at) VALUES (1,?,?,?,?,?,?,?,?)",
            (_DEFAULTS["aktif"], secrets.token_urlsafe(24), _DEFAULTS["profil"],
             _DEFAULTS["deadline_ms"], _DEFAULTS["fallback"],
             _DEFAULTS["pakai_riwayat"], _DEFAULTS["riwayat_turn"],
             time.strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
    else:
        r = conn.execute("SELECT token FROM df_webhook_config WHERE id=1").fetchone()
        if not (r and (r["token"] or "").strip()):
            conn.execute("UPDATE df_webhook_config SET token=? WHERE id=1",
                         (secrets.token_urlsafe(24),))
            conn.commit()
    return conn


def _row_to_dict(r):
    d = dict(r)
    d["aktif"] = bool(d.get("aktif"))
    d["pakai_riwayat"] = bool(d.get("pakai_riwayat"))
    try:
        d["deadline_ms"] = int(d.get("deadline_ms") or 4500)
    except Exception:
        d["deadline_ms"] = 4500
    try:
        d["riwayat_turn"] = int(d.get("riwayat_turn") or 3)
    except Exception:
        d["riwayat_turn"] = 3
    d["profil"] = (d.get("profil") or "chatbot").strip() or "chatbot"
    d["fallback"] = d.get("fallback") or FALLBACK_DEFAULT
    d["token"] = (d.get("token") or "").strip()
    return d


def get_config():
    conn = init_db(connect())
    try:
        r = conn.execute("SELECT * FROM df_webhook_config WHERE id=1").fetchone()
        return _row_to_dict(r)
    finally:
        conn.close()


def save_config(data):
    data = data or {}
    conn = init_db(connect())
    try:
        cur = get_config()
        aktif = 1 if data.get("aktif", cur["aktif"]) else 0
        profil = (data.get("profil") or cur["profil"] or "chatbot").strip() or "chatbot"
        try:
            deadline_ms = int(data.get("deadline_ms", cur["deadline_ms"]) or 4500)
        except Exception:
            deadline_ms = 4500
        deadline_ms = max(500, min(deadline_ms, 15000))
        fallback = (data.get("fallback") if data.get("fallback") is not None
                    else cur["fallback"]) or FALLBACK_DEFAULT
        pakai_riwayat = 1 if data.get("pakai_riwayat", cur["pakai_riwayat"]) else 0
        try:
            riwayat_turn = int(data.get("riwayat_turn", cur["riwayat_turn"]) or 3)
        except Exception:
            riwayat_turn = 3
        riwayat_turn = max(0, min(riwayat_turn, 10))
        conn.execute(
            "UPDATE df_webhook_config SET aktif=?,profil=?,deadline_ms=?,fallback=?,"
            "pakai_riwayat=?,riwayat_turn=?,updated_at=? WHERE id=1",
            (aktif, profil, deadline_ms, fallback, pakai_riwayat, riwayat_turn,
             time.strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
        return get_config()
    finally:
        conn.close()


def rotate_token():
    conn = init_db(connect())
    try:
        tok = secrets.token_urlsafe(24)
        conn.execute("UPDATE df_webhook_config SET token=?,updated_at=? WHERE id=1",
                     (tok, time.strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        return get_config()
    finally:
        conn.close()
