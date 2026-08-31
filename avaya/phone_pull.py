# -*- coding: utf-8 -*-
"""avaya/phone_pull.py - Fase 3 tahap 1 (TARIK): ambil s.d. N baris Telepon,
unduh audio DASH, simpan metadata ke awe_phone_interactions (belum STT/LLM).

Terpisah dari phone.py (berkas tetap kecil & aman push) dan dari alur Chat.
Memakai AvayaPhoneClient yang SUDAH login, lalu phone_dash.download_and_save.
"""
import avaya.phone_dash as pdash

try:
    from .phone_db import stage_phone_pull
except Exception:
    from phone_db import stage_phone_pull

_ROW_KEYS = ("sid", "site_id", "audio_ch_num", "audio_module_num", "ani",
             "dnis", "call_id", "interaction_type_id", "personal_id")


def _dur_seconds(iso):
    """Durasi ISO-8601 DASH (mis. 'PT52.224S', 'PT1M5S') -> detik (int)."""
    s = str(iso or "").strip().upper()
    if not s.startswith("PT"):
        return 0
    s = s[2:]
    total = 0.0
    num = ""
    unit = {"H": 3600, "M": 60, "S": 1}
    for ch in s:
        if ch.isdigit() or ch == ".":
            num += ch
        elif ch in unit:
            try:
                total += float(num or 0) * unit[ch]
            except Exception:
                pass
            num = ""
    return int(round(total))


def _rows_from_search(client, day_from, day_to, limit):
    """Cari interaksi Phone; kembalikan (search_id, n_rows_total, baris_audio)."""
    client._probe_itype = "1"
    sid = client.create_search(str(day_from)[:10] + "T00:00:00",
                               str(day_to)[:10] + "T23:59:59")
    client.exec_search(sid)
    info = client.get_header(sid)
    data = client.get_data(sid)
    cm = client.col_map(info.get("header") or [])

    def _txt(row, key):
        i = cm.get(key)
        if i is None or i >= len(row) or not isinstance(row[i], dict):
            return ""
        c = row[i]
        return str(c.get("Text") or c.get("Date") or c.get("ItemId") or "").strip()

    def _dat(row, key):
        i = cm.get(key)
        if i is None or i >= len(row) or not isinstance(row[i], dict):
            return ""
        c = row[i]
        return str(c.get("Date") or c.get("Text") or "").strip()

    try:
        cap = max(1, int(limit or 25))
    except Exception:
        cap = 25
    out = []
    for row in data:
        if not (_txt(row, "audio_ch_num") and _txt(row, "audio_module_num")):
            continue
        r = {k: _txt(row, k) for k in _ROW_KEYS}
        r["gmt"] = _dat(row, "audio_start_time_gmt")
        if r.get("sid"):
            out.append(r)
        if len(out) >= cap:
            break
    return sid, len(data), out


def _download_audio(client, r):
    """GetMedia -> manifest -> unduh DASH. Kembalikan (audio_ref, durasi, catatan)."""
    media = client.get_media(r["sid"], r["site_id"], r["audio_ch_num"],
                             r["audio_module_num"], r.get("gmt") or "",
                             cli=r.get("personal_id") or "")
    mj = media.get("json") if isinstance(media, dict) else None
    items = mj.get("mediaInfo") if isinstance(mj, dict) else None
    audio = {}
    if isinstance(items, list):
        audio = next((it for it in items if isinstance(it, dict)
                      and it.get("MediaType") == "Audio"), {}) or {}
    http_path = audio.get("HttpPath") or ""
    vwt = audio.get("VWT") or ""
    if not (http_path and vwt):
        return "", 0, "locator kosong"
    attempts = client.fetch_manifest(http_path, vwt)
    if not any(a.get("http_status") == 200 and a.get("looks_like_dash") for a in attempts):
        return "", 0, "manifest bukan DASH"
    try:
        dl = pdash.download_and_save(client, http_path, vwt)
    except Exception as e:
        return "", 0, "unduh error: %r" % e
    path = dl.get("saved_path") or ""
    dur = _dur_seconds((dl.get("manifest") or {}).get("duration"))
    note = "seg %s/%s = %s B" % (dl.get("segments_ok"), dl.get("segments"), dl.get("total_bytes"))
    return path, dur, note


def pull_day(client, conn, day_from, day_to=None, limit=25, pulled_by=None, download=True):
    """TARIK harian: cari baris audio, unduh, simpan metadata (tanpa STT/LLM)."""
    day_to = day_to or day_from
    day = str(day_from)[:10]
    search_id, n_rows, rows = _rows_from_search(client, day_from, day_to, limit)
    staged = []
    details = []
    for r in rows:
        audio_ref, dur, note = ("", 0, "unduh dilewati")
        if download:
            audio_ref, dur, note = _download_audio(client, r)
        staged.append({
            "sid": r["sid"], "day": day, "tanggal": r.get("gmt"),
            "ani": r.get("ani"), "dnis": r.get("dnis"), "call_id": r.get("call_id"),
            "site_id": r.get("site_id"), "durasi": dur, "audio_ref": audio_ref,
            "has_audio": 1 if audio_ref else 0,
        })
        details.append({"sid": r["sid"], "audio": bool(audio_ref),
                        "durasi": dur, "note": note})
    saved = stage_phone_pull(conn, day, staged, pulled_by=pulled_by)
    return {"ok": True, "day": day, "search_id": search_id,
            "n_rows_total": n_rows, "n_audio_rows": len(rows),
            "staged": saved.get("staged", 0),
            "with_audio": sum(1 for d in details if d["audio"]), "details": details}
