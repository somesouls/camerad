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
from pipeline.helpers import run_dir, _wb_from_bytes, read_sheet, _sv, _find_header

try:
    import db.analytics_db as adb
except Exception:
    adb = None

_orig_step6_load = pr.step6_load

SID_HEADERS = [
    "ID Percakapan", "ID_Percakapan", "ID Sesi", "ID_Sesi",
    "session_id", "SessionId", "Session ID", "ID trace", "ID Trace", "id_trace"
]
INS_HEADERS = ["InsertId", "InserId", "insertId", "insert_id"]


def _row_to_session_and_insertid(cfg, ctx):
    """Map nomor baris sheet 'Analisis Fallback' -> (row_to_sid, row_to_ins).
    
    Prioritas session_id:
    1. Kolom 'ID Percakapan' / 'ID Sesi' / 'session_id' langsung dari sheet (100% akurat).
    2. Fallback: bila kolom ID Percakapan tidak ada/kosong, petakan InsertId -> session_id
       via tabel interactions di analytics.db.
    """
    row_to_sid = {}
    row_to_ins = {}
    missing_ins_for_sid = {}
    try:
        p = os.path.join(run_dir(cfg, ctx.run), "step6_source.xlsx")
        if not os.path.isfile(p):
            return row_to_sid, row_to_ins
        with open(p, "rb") as f:
            b = f.read()
        wb = _wb_from_bytes(b)
        if "Analisis Fallback" not in wb.sheetnames:
            return row_to_sid, row_to_ins
        sh = read_sheet(wb["Analisis Fallback"])
        H = sh.get("headers") or {}
        c_sid = _find_header(H, SID_HEADERS)
        c_ins = _find_header(H, INS_HEADERS)
        if not c_sid and not c_ins:
            return row_to_sid, row_to_ins
        for rn, cells in sh.get("rows", {}).items():
            if rn == 1:
                continue
            sid = _sv(cells, c_sid).strip() if c_sid else ""
            ins = _sv(cells, c_ins).strip() if c_ins else ""
            if ins:
                row_to_ins[rn] = ins
            if sid:
                row_to_sid[rn] = sid
            elif ins:
                missing_ins_for_sid.setdefault(ins, []).append(rn)
        # Fallback via analytics.db HANYA jika sid kosong tapi ada ins
        if missing_ins_for_sid and adb is not None:
            i2s = _insertid_to_session(list(missing_ins_for_sid.keys()))
            for ins_val, rns in missing_ins_for_sid.items():
                found_sid = i2s.get(ins_val)
                if found_sid:
                    for rn in rns:
                        row_to_sid[rn] = found_sid
    except Exception:
        pass
    return row_to_sid, row_to_ins


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
            r2s, r2i = _row_to_session_and_insertid(cfg, ctx)
            for r in rows:
                rn = r.get("row")
                ins = r2i.get(rn)
                if ins:
                    r["insert_id"] = ins
                sid = r2s.get(rn)
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
