# -*- coding: utf-8 -*-
"""content_search.py — Pencarian ISI (regex) SISI-SERVER, lintas database.

Motivasi: mode text-to-SQL biasa cenderung mencari di kolom IDENTITAS
(customer/ani/nik) dan sulit menyaring POLA pada ISI percakapan yang tersimpan
sebagai teks JSON transkrip. Selain itu, masker PII mengubah email/NIK menjadi
placeholder sebelum sampai ke LLM, sehingga pencocokan pola email mustahil di
sisi model.

Modul ini mencocokkan POLA di SERVER (Python re) langsung pada teks isi, lalu
hanya mengembalikan kolom identitas aman (mis. SID/Nama/NIK atau ID/tanggal) --
isi/email mentah tidak perlu dikirim ke LLM. Privasi terjaga, pencarian isi
menjadi mungkin.

Cakupan (E — generalisasi):
- 'avaya' : jalur khusus & presisi (chat: awe_conversations.transkrip_json;
            phone: awe_phone_interactions.stt_text/transkrip_json/ringkasan),
            termasuk deteksi bot-only via peran pesan transkrip.
- 'analytics', 'sosmed', 'agent_log' : jalur generik atas kolom teks masing-masing.

READ-ONLY & ADITIF: hanya membuka koneksi baca lalu menjalankan SELECT/PRAGMA.
Tidak mengubah data. Database `users` tidak tersentuh.
"""
import json as _json
import re as _re
import importlib as _importlib

try:
    from avaya.db import connect as _avaya_connect, _is_agent, _name_wo_nik
except Exception:  # pragma: no cover - fallback bila import gagal
    _avaya_connect = None

    def _is_agent(role, text):
        return bool((role or "").strip())

    def _name_wo_nik(s):
        s = str(s or "")
        i = s.find("[")
        return (s[:i] if i >= 0 else s).strip()

MAX_SCAN = 20000       # batas baris yang dipindai (aman untuk data besar)
MAX_RESULTS = 2000     # batas jumlah hasil yang dikembalikan


# Konfigurasi jalur GENERIK untuk database non-avaya. Tiap scope menetapkan
# tabel, kolom teks yang dipindai, kolom identitas yang dikembalikan (aman),
# dan kolom tanggal opsional. Koneksi dibuka via modul terdaftar (punya connect()).
_GENERIC = {
    "sosmed": {
        "module": "sosmed.db",
        "items": {
            "table": "sosmed_items",
            "text_cols": ["text"],
            "ret": [("id", "id"), ("author", "author"), ("item_type", "item_type")],
            "date_col": "ts",
        },
    },
    "analytics": {
        "module": "db.analytics_db",
        "chat": {
            "table": "interactions",
            "text_cols": ["user_phrase", "bot_response"],
            "ret": [("session_id", "session_id"), ("intent_name", "intent")],
            "date_col": "day",
        },
    },
    "agent_log": {
        "module": "db.agent_log_db",
        "chat": {
            "table": "rag_chat_log",
            "text_cols": ["content"],
            "ret": [("session_id", "session_id"), ("role", "role")],
            "date_col": "ts",
        },
    },
}


def _texts_of(v):
    """Gabungkan teks dari nilai transkrip: JSON list [{role,text}] atau teks polos."""
    if v is None:
        return ""
    s = str(v)
    st = s.lstrip()
    if st.startswith("[") or st.startswith("{"):
        try:
            obj = _json.loads(s)
        except Exception:
            return s
        if isinstance(obj, list):
            return "\n".join(
                (str(m.get("text", "")) if isinstance(m, dict) else str(m))
                for m in obj)
        if isinstance(obj, dict):
            return str(obj.get("text", "") or obj.get("stt_text", "") or "")
        return s
    return s


def _has_agent(v):
    """True bila transkrip memuat minimal satu pesan agen (bukan murni bot)."""
    if v is None:
        return False
    s = str(v)
    if not s.lstrip().startswith("["):
        return True  # teks polos (mis. stt telepon): anggap ada interaksi agen
    try:
        tx = _json.loads(s)
    except Exception:
        return True
    if not isinstance(tx, list):
        return True
    return any(
        isinstance(m, dict) and _is_agent(m.get("role", ""), m.get("text", ""))
        for m in tx)


def _compile(pattern, ignorecase):
    flags = (_re.IGNORECASE | _re.DOTALL) if ignorecase else _re.DOTALL
    return _re.compile(pattern, flags)


def supported():
    """Daftar db_key yang didukung content_search (untuk introspeksi/grounding)."""
    return ["avaya"] + list(_GENERIC.keys())


def _search_avaya(rx, scope, exclude_bot_only, prefilter, lim):
    if _avaya_connect is None:
        return {"ok": False, "error": "modul avaya.db tidak tersedia."}
    scope = (scope or "chat").strip().lower()
    conn = _avaya_connect()
    try:
        cur = conn.cursor()
        rows_out = []
        scanned = 0
        if scope == "phone":
            have = {r[1] for r in cur.execute(
                "PRAGMA table_info(awe_phone_interactions)").fetchall()}
            tcols = [c for c in ("stt_text", "transkrip_json", "ringkasan") if c in have]
            if not tcols:
                return {"ok": False,
                        "error": "tidak ada kolom teks pada awe_phone_interactions."}
            sql = ("SELECT sid, customer, tanggal, " + ",".join(tcols) +
                   " FROM awe_phone_interactions WHERE 1=1")
            params = []
            if prefilter:
                sql += " AND (" + " OR ".join(c + " LIKE ?" for c in tcols) + ")"
                params += ["%" + prefilter + "%"] * len(tcols)
            sql += " ORDER BY rowid DESC LIMIT ?"
            params.append(MAX_SCAN)
            for r in cur.execute(sql, params).fetchall():
                scanned += 1
                blob = "\n".join(_texts_of(r[c]) for c in tcols)
                if not blob.strip() or not rx.search(blob):
                    continue
                rows_out.append([r["sid"], _name_wo_nik(r["customer"] or ""), "",
                                 str(r["tanggal"] or "")[:10]])
                if len(rows_out) >= lim:
                    break
            return {"ok": True, "columns": ["sid", "nama", "nik", "tanggal"],
                    "rows": rows_out, "total": len(rows_out), "scanned": scanned,
                    "truncated": len(rows_out) >= lim, "scope": "phone", "db": "avaya"}
        # default: chat
        have = {r[1] for r in cur.execute(
            "PRAGMA table_info(awe_conversations)").fetchall()}
        if "transkrip_json" not in have:
            return {"ok": False, "error": "kolom transkrip_json tidak tersedia."}
        sql = ("SELECT sid, customer, nik, agent_name, tanggal, transkrip_json "
               "FROM awe_conversations WHERE transkrip_json IS NOT NULL")
        params = []
        if prefilter:
            sql += " AND transkrip_json LIKE ?"
            params.append("%" + prefilter + "%")
        sql += " ORDER BY rowid DESC LIMIT ?"
        params.append(MAX_SCAN)
        for r in cur.execute(sql, params).fetchall():
            scanned += 1
            tj = r["transkrip_json"]
            if exclude_bot_only and not _has_agent(tj):
                continue
            blob = _texts_of(tj)
            if not blob.strip() or not rx.search(blob):
                continue
            rows_out.append([r["sid"], _name_wo_nik(r["customer"] or ""),
                             r["nik"] or "", str(r["tanggal"] or "")[:10]])
            if len(rows_out) >= lim:
                break
        return {"ok": True, "columns": ["sid", "nama", "nik", "tanggal"],
                "rows": rows_out, "total": len(rows_out), "scanned": scanned,
                "truncated": len(rows_out) >= lim, "scope": "chat", "db": "avaya"}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _search_generic(db_key, cfg_all, rx, scope, prefilter, lim):
    scopes = {k: v for k, v in cfg_all.items() if k != "module"}
    scope = (scope or "").strip().lower()
    cfg = scopes.get(scope) or (next(iter(scopes.values())) if scopes else None)
    if not cfg:
        return {"ok": False, "error": "scope tidak dikenal untuk '%s'." % db_key}
    try:
        mod = _importlib.import_module(cfg_all.get("module"))
        conn = mod.connect()
    except Exception as e:
        return {"ok": False, "error": "gagal membuka database '%s': %s" % (db_key, e)}
    try:
        cur = conn.cursor()
        table = cfg["table"]
        have = {r[1] for r in cur.execute("PRAGMA table_info(%s)" % table).fetchall()}
        text_cols = [c for c in cfg.get("text_cols", []) if c in have]
        if not text_cols:
            return {"ok": False, "error": "tidak ada kolom teks pada %s." % table}
        ret = [(c, alias) for c, alias in cfg.get("ret", []) if c in have]
        ret_cols = [c for c, _ in ret]
        ret_names = [alias for _, alias in ret]
        date_col = cfg.get("date_col") if cfg.get("date_col") in have else None
        sel = list(ret_cols)
        if date_col:
            sel.append(date_col)
        sel_all = sel + text_cols
        sql = "SELECT " + ",".join(sel_all) + " FROM " + table + " WHERE 1=1"
        params = []
        if prefilter:
            sql += " AND (" + " OR ".join(c + " LIKE ?" for c in text_cols) + ")"
            params += ["%" + prefilter + "%"] * len(text_cols)
        sql += " ORDER BY rowid DESC LIMIT ?"
        params.append(MAX_SCAN)
        n_ret = len(ret_cols)
        n_date = 1 if date_col else 0
        rows_out = []
        scanned = 0
        for r in cur.execute(sql, params).fetchall():
            scanned += 1
            blob = "\n".join(_texts_of(r[n_ret + n_date + i]) for i in range(len(text_cols)))
            if not blob.strip() or not rx.search(blob):
                continue
            out_row = [r[i] for i in range(n_ret)]
            if date_col:
                out_row.append(str(r[n_ret] or "")[:10])
            rows_out.append(out_row)
            if len(rows_out) >= lim:
                break
        cols = list(ret_names) + (["tanggal"] if date_col else [])
        return {"ok": True, "columns": cols, "rows": rows_out,
                "total": len(rows_out), "scanned": scanned,
                "truncated": len(rows_out) >= lim, "scope": scope or "default",
                "db": db_key}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def search(db_key, pattern, scope="chat", exclude_bot_only=True,
           prefilter="", limit=500, ignorecase=True):
    """Cari POLA regex pada ISI. Return dict siap-JSON.

    Args:
      db_key: 'avaya' (khusus) atau salah satu dari analytics/sosmed/agent_log.
      pattern: regex Python (dicocokkan ke gabungan teks isi).
      scope: untuk avaya 'chat'/'phone'; untuk generik nama scope (mis. 'items'/'chat').
      exclude_bot_only: (khusus avaya-chat) buang percakapan tanpa pesan agen.
      prefilter: substring opsional untuk mempersempit kandidat di SQL lebih dulu
                 (mis. 'gmail.com') agar tidak memindai semua baris.
      limit: maksimum baris hasil (di-clamp ke MAX_RESULTS).
      ignorecase: pencocokan tidak peka huruf (default True) + DOTALL.

    Returns: {ok, columns, rows, total, scanned, truncated, scope, db}
             atau {ok:False, error}.
    """
    key = (db_key or "").strip().lower()
    try:
        rx = _compile(pattern, ignorecase)
    except Exception as e:
        return {"ok": False, "error": "pola regex tidak valid: %s" % e}
    try:
        lim = max(1, min(int(limit or 500), MAX_RESULTS))
    except Exception:
        lim = 500
    if key == "avaya":
        return _search_avaya(rx, scope, exclude_bot_only, prefilter, lim)
    if key in _GENERIC:
        return _search_generic(key, _GENERIC[key], rx, scope, prefilter, lim)
    return {"ok": False,
            "error": "content_search belum mendukung database '%s' (didukung: %s)."
                     % (db_key, ", ".join(supported()))}
