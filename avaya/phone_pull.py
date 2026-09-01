# -*- coding: utf-8 -*-
"""avaya/phone_pull.py - Fase 3 tahap 1 (TARIK): ambil interaksi Telepon,
unduh audio DASH, simpan metadata ke awe_phone_interactions (belum STT/LLM).

Terpisah dari phone.py (berkas tetap kecil & aman push) dan dari alur Chat.
Memakai AvayaPhoneClient yang SUDAH login, lalu phone_dash.download_and_save.

Pencarian dipecah per waktu secara rekursif meniru penarikan Livechat
(avaya.client.collect_window): bila satu rentang terpotong (>=2000 baris atau
maxExceeded) rentang dibelah dua sampai muat, sehingga bisa menarik LEBIH dari
2000 interaksi per hari. Argumen 'limit' HANYA membatasi jumlah audio yang
benar-benar diunduh; None / 0 / negatif / 'semua' = TANPA batas (semua).
"""
import datetime as _dt

import avaya.phone_dash as pdash

try:
    from .phone_db import stage_phone_pull
except Exception:
    from phone_db import stage_phone_pull

_ROW_KEYS = ("sid", "site_id", "audio_ch_num", "audio_module_num", "ani",
             "dnis", "call_id", "interaction_type_id", "personal_id",
             "personal_name")

_DATA_CAP = 2000        # ambang "terpotong" (hasil pencarian maks ~2000 baris)
_MIN_WINDOW_SEC = 120   # jangan belah rentang lebih halus dari 2 menit


def _agent_name(raw):
    """personal_name 'Belakang, Depan' -> 'Depan Belakang' (nama agen)."""
    s = str(raw or "").strip()
    if "," in s:
        last, first = s.split(",", 1)
        last, first = last.strip(), first.strip()
        if last and first:
            return first + " " + last
    return s


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


def _norm_cap(limit):
    """Batas tarik -> int>0 atau None (= SEMUA).

    Terima angka/str; 0, kosong, negatif, 'all'/'semua'/'none' -> None (semua).
    """
    if limit is None:
        return None
    s = str(limit).strip().lower()
    if s in ("", "0", "-1", "all", "semua", "none"):
        return None
    try:
        v = int(float(s))
    except Exception:
        return 25
    return v if v > 0 else None


def _fmt_local(dtobj):
    return dtobj.strftime("%Y-%m-%dT%H:%M:%S")


def _parse_local(s):
    return _dt.datetime.strptime(str(s)[:19], "%Y-%m-%dT%H:%M:%S")


def _cell(row, cm, key, date=False):
    i = cm.get(key)
    if i is None or i >= len(row) or not isinstance(row[i], dict):
        return ""
    c = row[i]
    if date:
        return str(c.get("Date") or c.get("Text") or "").strip()
    return str(c.get("Text") or c.get("Date") or c.get("ItemId") or "").strip()


def _collect_window(client, frm, to, acc, cap=None, on_prog=None, stats=None):
    """Kumpulkan baris audio unik (by sid) pada [frm,to].

    Bila hasil terpotong (>=2000 / maxExceeded) & rentang > _MIN_WINDOW_SEC,
    belah dua secara waktu lalu rekursif (meniru collect_window Livechat).
    Berhenti awal begitu 'cap' terpenuhi (urut kronologis ASC = paling awal).
    """
    if cap and len(acc) >= cap:
        return
    sid = client.create_search(frm, to)
    client.exec_search(sid)
    info = client.get_header(sid)
    data = client.get_data(sid)
    if stats is not None:
        stats["windows"] = stats.get("windows", 0) + 1
        stats["scanned"] = stats.get("scanned", 0) + len(data)
    capped = info.get("maxExceeded") or len(data) >= _DATA_CAP
    if on_prog:
        on_prog("Rentang %s..%s -> %d baris%s" % (
            frm[11:16], to[11:16], len(data) or info.get("count") or 0,
            " (>2000, dipecah)" if capped else ""))
    if capped and (_parse_local(to) - _parse_local(frm)).total_seconds() > _MIN_WINDOW_SEC:
        mid = _parse_local(frm) + (_parse_local(to) - _parse_local(frm)) / 2
        _collect_window(client, frm, _fmt_local(mid), acc, cap, on_prog, stats)
        _collect_window(client, _fmt_local(mid + _dt.timedelta(seconds=1)), to,
                        acc, cap, on_prog, stats)
        return
    cm = client.col_map(info.get("header") or [])
    for row in data:
        if not (_cell(row, cm, "audio_ch_num") and _cell(row, cm, "audio_module_num")):
            continue
        sidv = _cell(row, cm, "sid")
        if not sidv or sidv in acc:
            continue
        r = {k: _cell(row, cm, k) for k in _ROW_KEYS}
        r["gmt"] = _cell(row, cm, "audio_start_time_gmt", date=True)
        acc[sidv] = r
        if cap and len(acc) >= cap:
            return


def _rows_from_search(client, day_from, day_to, limit, on_prog=None):
    """Cari interaksi Phone (auto-pecah waktu). Kembalikan (stats, baris_audio)."""
    client._probe_itype = "1"
    frm = str(day_from)[:10] + "T00:00:00"
    to = str(day_to)[:10] + "T23:59:59"
    cap = _norm_cap(limit)
    acc = {}
    stats = {"windows": 0, "scanned": 0}
    if on_prog:
        on_prog("Mencari interaksi %s..%s%s" % (
            str(day_from)[:10], str(day_to)[:10],
            "" if cap is None else (" (maks %d)" % cap)))
    _collect_window(client, frm, to, acc, cap, on_prog, stats)
    return stats, list(acc.values())


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


def pull_day(client, conn, day_from, day_to=None, limit=25, pulled_by=None,
             download=True, on_prog=None):
    """TARIK: cari baris audio (auto-pecah waktu), unduh, simpan metadata."""
    day_to = day_to or day_from
    stats, rows = _rows_from_search(client, day_from, day_to, limit, on_prog)
    total = len(rows)
    if on_prog and total:
        on_prog("Ditemukan %d panggilan beraudio; mulai unduh audio..." % total)
    staged = []
    details = []
    for i, r in enumerate(rows):
        audio_ref, dur, note = ("", 0, "unduh dilewati")
        if download:
            audio_ref, dur, note = _download_audio(client, r)
        rday = (str(r.get("gmt") or "")[:10]) or str(day_from)[:10]
        staged.append({
            "sid": r["sid"], "day": rday, "tanggal": r.get("gmt"),
            "ani": r.get("ani"), "dnis": r.get("dnis"), "call_id": r.get("call_id"),
            "site_id": r.get("site_id"), "durasi": dur, "audio_ref": audio_ref,
            "has_audio": 1 if audio_ref else 0,
            "agent_name": _agent_name(r.get("personal_name")),
        })
        details.append({"sid": r["sid"], "audio": bool(audio_ref),
                        "durasi": dur, "note": note})
        if on_prog and download and total > 20 and (i + 1) % 10 == 0:
            on_prog("Unduh audio %d/%d..." % (i + 1, total))
    saved = stage_phone_pull(conn, str(day_from)[:10], staged, pulled_by=pulled_by)
    return {"ok": True, "day": str(day_from)[:10], "windows": stats.get("windows", 0),
            "n_rows_total": stats.get("scanned", 0), "n_audio_rows": total,
            "staged": saved.get("staged", 0),
            "with_audio": sum(1 for d in details if d["audio"]), "details": details}
