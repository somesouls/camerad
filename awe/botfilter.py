# -*- coding: utf-8 -*-
"""awe/botfilter.py — Util bersama untuk mengecualikan percakapan "bot only"
(mis. "Chatbot, Google" / CCAI / Virtual Assistant) dari seluruh menu AWE Chat.

Dipakai oleh awe.analytics, awe.overview, awe.daily_users, dan awe.assess.

Filosofi:
  - "bot only" = kolom agent_name menandakan bot (chatbot/ccai/virtual assistant/
    google). Baris ini berasal dari Google CCAI dan datanya sudah tercakup di
    Dialogflow, sehingga default DIKELUARKAN dari tampilan menu AWE Chat.
  - Baris dengan agent_name KOSONG TIDAK dikecualikan (itu self-service
    pelanggan, bukan bot).
  - Pengecualian bisa dimatikan lewat query param ?exclude_bot=0 (checkbox UI).
"""
import re

# Penanda nama agent yang berarti percakapan ditangani bot, bukan agent manusia.
BOT_MARKERS = ("chatbot", "ccai", "virtual assistant", "google")

_BOT_NAME_RE = re.compile(r"chatbot|ccai|virtual\s+assistant|google", re.I)

# Nilai query-param yang berarti "jangan kecualikan" (checkbox tidak dicentang).
_OFF_VALUES = ("0", "false", "no", "off", "tidak")


def is_bot_name(name):
    """True bila agent_name menandakan percakapan bot only."""
    return bool(_BOT_NAME_RE.search(str(name or "")))


def wants_exclude(query_params):
    """Baca flag exclude_bot dari query params.

    Default: True (kecualikan bot only) bila param tidak ada. Kembalikan False
    hanya bila nilainya termasuk _OFF_VALUES.
    """
    try:
        v = query_params.get("exclude_bot")
    except Exception:
        v = None
    if v is None:
        return True
    return str(v).strip().lower() not in _OFF_VALUES


def exclude_bot_sql(col="agent_name"):
    """Kondisi SQL untuk membuang baris bot only pada kolom `col`.

    Aman digabung ke daftar WHERE dengan AND. Tidak butuh parameter (literal
    inline). Baris dengan agent_name kosong tetap lolos (bukan bot).
    """
    c = "LOWER(COALESCE(%s,''))" % col
    return (
        "(" + c + " NOT LIKE '%chatbot%' AND "
        + c + " NOT LIKE '%ccai%' AND "
        + c + " NOT LIKE '%virtual assistant%' AND "
        + c + " NOT LIKE '%google%')"
    )
