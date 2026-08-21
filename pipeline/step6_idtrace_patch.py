# -*- coding: utf-8 -*-
"""step6_idtrace_patch.py — Fase 2.

Menambahkan `id_trace` (Dialogflow session_id) ke tiap baris Step 6 supaya
tombol 'mata' (lihat percakapan penuh) di UI bisa memanggil
/api/deflection/transcript?session_id=<id_trace>.

Cara kerja: bungkus pr.step6_load. Baris Step 6 hanya membawa nomor baris sheet
('row') dari sheet 'Analisis Fallback'; sheet itu punya kolom InsertId. Kita
petakan nomor baris -> InsertId dari step6_source.xlsx, lalu InsertId ->
session_id dari tabel interactions (analytics.db). Fail-open: bila apa pun gagal,
baris dikembalikan apa adanya (tanpa id_trace) sehingga app tetap aman.

Urutan impor di web_app.py: setelah step6_patch (biar sinyal & id_trace sama-sama
terpasang; kedua patch membungkus pr.step6_load secara berantai).
"""
import os

import pipeline.routes as pr
from pipeline.helpers import run_dir, _wb_from_bytes, read_sheet, _sv

try:
    import db.analytics_db as adb
except Exception:
    adb = None

_orig_step6_load = pr.step6_load


def _row_to_insertid(cfg, ctx):
    """Map nomor baris sheet 'Analisis Fallback' -> InsertId."""
    out = {}
    try:
        p = os.path.join(run_dir(cfg, ctx.run), "step6_source.xlsx")
        if not os.path.isfile(p):
            return out
        with open(p, "rb") as f:
            b = f.read()
        wb = _wb_from_bytes(b)
        if "Analisis Fallback" not in wb.sheetnames:
            return out
        sh = read_sheet(wb["Analisis Fallback"])
        H = sh["headers"]
        c_ins = H.get("InsertId") or H.get("InserId")
        if not c_ins:
            return out
        for rn, cells in sh["rows"].items():
            if rn == 1:
                continue
            ins = _sv(cells, c_ins).strip()
            if ins:
                out[rn] = ins
    except Exception:
        pass
    return out


def _insertid_to_session(insert_ids):
    """Map InsertId -> session_id dari tabel interactions (analytics.db)."""
    out = {}
    if adb is None or not insert_ids:
        return out
    ids = [i for i in insert_ids if i]
    if not ids:
        return out
    try:
        conn = adb.connect()
        try:
            CH = 400
            for i in range(0, len(ids), CH):
                chunk = ids[i:i + CH]
                qmarks = ",".join(["?"] * len(chunk))
                rows = conn.execute(
                    "SELECT insert_id, session_id FROM interactions "
                    "WHERE insert_id IN (" + qmarks + ")",
                    chunk,
                ).fetchall()
                for r in rows:
                    sid = r["session_id"] or ""
                    if sid:
                        out[r["insert_id"]] = sid
        finally:
            conn.close()
    except Exception:
        pass
    return out


def step6_load(cfg, ctx):
    res = _orig_step6_load(cfg, ctx)
    try:
        rows = res.get("rows") or []
        if rows:
            r2i = _row_to_insertid(cfg, ctx)
            i2s = _insertid_to_session(list(set(r2i.values())))
            for r in rows:
                ins = r2i.get(r.get("row"))
                if ins:
                    r["insert_id"] = ins
                    sid = i2s.get(ins)
                    if sid:
                        r["id_trace"] = sid
    except Exception:
        pass
    return res


pr.step6_load = step6_load

try:
    print("[step6_idtrace_patch] aktif: id_trace untuk Step 6 (mata percakapan)", flush=True)
except Exception:
    pass
