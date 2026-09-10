# -*- coding: utf-8 -*-
"""content_search.py — Pencarian ISI percakapan (regex) SISI-SERVER.

Motivasi: mode text-to-SQL biasa cenderung mencari di kolom IDENTITAS
(customer/ani/nik) dan tidak bisa menyaring POLA pada ISI percakapan yang
tersimpan sebagai teks JSON transkrip. Selain itu, masker PII mengubah
email/NIK menjadi placeholder sebelum sampai ke LLM, sehingga pencocokan pola
email mustahil dilakukan di sisi model.

Modul ini melakukan pencocokan di SERVER (Python re) langsung pada teks
transkrip, lalu hanya mengembalikan SID/Nama/NIK -- email/isi mentah tidak perlu
dikirim ke LLM. Dengan begitu privasi tetap terjaga sekaligus pencarian isi
menjadi mungkin.

READ-ONLY & ADITIF: hanya membuka koneksi baca avaya.db lalu menjalankan
SELECT/PRAGMA. Tidak mengubah data. Database `users` tidak tersentuh.
"""
import json as _json
import re as _re

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


def search(db_key, pattern, scope="chat", exclude_bot_only=True,
           prefilter="", limit=500, ignorecase=True):
    """Cari POLA regex pada ISI percakapan. Return dict siap-JSON.

    Args:
      db_key: harus 'avaya'.
      pattern: regex Python (dicocokkan ke gabungan teks transkrip).
      scope: 'chat' (awe_conversations.transkrip_json) atau 'phone'
             (awe_phone_interactions.stt_text/transkrip_json/ringkasan).
      exclude_bot_only: bila True, buang percakapan tanpa pesan agen (murni bot).
      prefilter: substring opsional untuk mempersempit kandidat di SQL lebih dulu
                 (mis. 'gmail.com') agar tidak memindai semua baris.
      limit: maksimum baris hasil (di-clamp ke MAX_RESULTS).
      ignorecase: pencocokan tidak peka huruf (default True) + DOTALL.

    Returns: {ok, columns:[sid,nama,nik,tanggal], rows, total, scanned,
              truncated, scope} atau {ok:False, error}.
    """
    if (db_key or "").strip().lower() != "avaya":
        return {"ok": False, "error": "content_search hanya tersedia untuk database 'avaya'."}
    if _avaya_connect is None:
        return {"ok": False, "error": "modul avaya.db tidak tersedia."}
    try:
        flags = (_re.IGNORECASE | _re.DOTALL) if ignorecase else _re.DOTALL
        rx = _re.compile(pattern, flags)
    except Exception as e:
        return {"ok": False, "error": "pola regex tidak valid: %s" % e}
    try:
        lim = max(1, min(int(limit or 500), MAX_RESULTS))
    except Exception:
        lim = 500
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
                    "truncated": len(rows_out) >= lim, "scope": "phone"}
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
                "truncated": len(rows_out) >= lim, "scope": "chat"}
    finally:
        try:
            conn.close()
        except Exception:
            pass
