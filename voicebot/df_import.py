# -*- coding: utf-8 -*-
"""voicebot/df_import.py -- impor intent + training phrase dari data Dialogflow.

Sumber data = tabel `interactions` di analytics.db (db.analytics_db), yaitu
interaksi Dialogflow yang sudah ditarik dari Google Cloud Logging. Kita ambil
intent "bersih" tersibuk (kecuali System_/Umum_/fallback), kumpulkan training
phrase (user_phrase unik paling sering) + satu respons perwakilan (bot_response
terbanyak), lalu upsert ke vb_intents (dipakai NLU lokal voicebot).

Semua diproses lokal; tidak ada panggilan cloud.
"""
import re

import db.analytics_db as adb
from voicebot import config_db as cfg


def _clean_where(include_system=False, include_umum=False):
    """Filter intent 'bersih' (samakan logika dashboard analytics_db)."""
    parts = [
        "intent_name IS NOT NULL",
        "TRIM(intent_name) <> ''",
        "is_fallback=0",
    ]
    if not include_system:
        parts.append("is_system=0")
        parts.append("substr(intent_name,1,7) <> 'System_'")
    if not include_umum:
        parts.append("substr(intent_name,1,5) <> 'Umum_'")
    return " AND ".join(parts)


def _filters(lang=None, start=None, end=None):
    where = ""
    params = []
    if lang:
        where += " AND lang=?"
        params.append(str(lang).lower())
    if start:
        where += " AND day>=?"
        params.append(start)
    if end:
        where += " AND day<=?"
        params.append(end)
    return where, params


def _norm(s):
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def preview_top_intents(limit=50, start=None, end=None, lang=None,
                        include_system=False, include_umum=False):
    """Daftar intent tersibuk (nama + jumlah) TANPA menulis ke DB."""
    conn = adb.init_db(adb.connect())
    try:
        base = _clean_where(include_system, include_umum)
        fw, params = _filters(lang, start, end)
        params.append(int(limit))
        rows = conn.execute(
            "SELECT intent_name, COUNT(*) AS c FROM interactions WHERE " +
            base + fw + " GROUP BY intent_name "
            "ORDER BY c DESC, intent_name ASC LIMIT ?",
            params,
        ).fetchall()
        return [{"intent": r["intent_name"], "count": r["c"]} for r in rows]
    finally:
        conn.close()


def _phrases_for(conn, intent, base, fw, fparams, max_phrases):
    rows = conn.execute(
        "SELECT user_phrase, COUNT(*) AS c FROM interactions WHERE " + base + fw +
        " AND intent_name=? AND TRIM(COALESCE(user_phrase,'')) <> '' "
        "GROUP BY user_phrase ORDER BY c DESC",
        fparams + [intent],
    ).fetchall()
    out, seen = [], set()
    for r in rows:
        ph = (r["user_phrase"] or "").strip()
        n = _norm(ph)
        if not n or n in seen:
            continue
        seen.add(n)
        out.append(ph)
        if len(out) >= int(max_phrases):
            break
    return out


def _response_for(conn, intent, base, fw, fparams):
    r = conn.execute(
        "SELECT bot_response, COUNT(*) AS c FROM interactions WHERE " + base + fw +
        " AND intent_name=? AND TRIM(COALESCE(bot_response,'')) <> '' "
        "GROUP BY bot_response ORDER BY c DESC LIMIT 1",
        fparams + [intent],
    ).fetchone()
    return (r["bot_response"].strip() if r and r["bot_response"] else "")


def import_top_intents(limit=50, max_phrases=40, min_count=1,
                       start=None, end=None, lang=None,
                       include_system=False, include_umum=False,
                       skip_existing=False, activate=True):
    """Impor intent tersibuk -> vb_intents. Return ringkasan lengkap."""
    top = preview_top_intents(limit=limit, start=start, end=end, lang=lang,
                              include_system=include_system,
                              include_umum=include_umum)
    base = _clean_where(include_system, include_umum)
    fw, fparams = _filters(lang, start, end)
    try:
        existing = {(i.get("name") or "") for i in cfg.list_intents()}
    except Exception:
        existing = set()
    conn = adb.init_db(adb.connect())
    imported = updated = skipped = 0
    total_phrases = 0
    details = []
    try:
        for it in top:
            name = it["intent"]
            if int(it["count"]) < int(min_count):
                continue
            already = name in existing
            if skip_existing and already:
                skipped += 1
                continue
            phrases = _phrases_for(conn, name, base, fw, fparams, max_phrases)
            response = _response_for(conn, name, base, fw, fparams)
            if not phrases:
                skipped += 1
                details.append({"intent": name, "count": it["count"],
                                "skipped": "tanpa training phrase"})
                continue
            try:
                cfg.upsert_intent({
                    "name": name,
                    "phrases": phrases,
                    "response": response,
                    "aktif": 1 if activate else 0,
                })
                if already:
                    updated += 1
                else:
                    imported += 1
                total_phrases += len(phrases)
                details.append({"intent": name, "count": it["count"],
                                "phrases": len(phrases),
                                "has_response": bool(response),
                                "mode": "update" if already else "baru"})
            except Exception as e:  # noqa: BLE001
                details.append({"intent": name, "error": str(e)[:200]})
        # reset cache NLU supaya intent baru langsung dipakai
        try:
            from voicebot import nlu as _n
            _n.reset_cache()
        except Exception:
            pass
        return {"ok": True, "considered": len(top),
                "imported": imported, "updated": updated, "skipped": skipped,
                "phrases": total_phrases, "details": details}
    finally:
        conn.close()
