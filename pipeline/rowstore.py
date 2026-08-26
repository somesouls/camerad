# -*- coding: utf-8 -*-
"""pipeline/rowstore.py — Lapisan BARIS (row-based) untuk Step 4-9.

Menyimpan hasil model & analisis sebagai BARIS terstruktur di pipeline_store.db
(tabel step_row) + editan analis (step_edit), bukan sebagai blob Excel. File
Excel dirakit ON-DEMAND dari baris + editan saat diunduh / dialirkan ke step
berikutnya. Menjawab keluhan: DB jadi 1, mudah dimuat/diedit/disimpan, dan
editan (mis. "Intent Seharusnya" Step 9) benar-benar persist (kunci bisnis
stabil, bukan nomor baris Excel).

Memakai util dari pipeline.helpers (H) untuk XLSX & resolusi dataset. helpers.py
mengimpor rowstore secara LAZY (di dalam fungsi) agar tak ada impor melingkar.
"""
import os
import datetime as _dt

from pipeline import store as pstore
from pipeline import helpers as H
from app_core import XLSX_MIME

# Kandidat kolom identitas (kunci bisnis stabil), urut prioritas.
_ROW_ID_DEFAULT = ["insertId", "InsertId", "InserId", "ID Rekaman", "ID Percakapan",
                   "id_rekaman", "ID trace", "ID Trace", "IDtrace"]


def _headers_meta_key(n):
    return "hdr_step%d" % int(n)


def _editsheet_meta_key(n):
    return "editsheet_step%d" % int(n)


def _cell_to_str(v):
    """Normalisasi nilai sel -> string (konsisten dgn perilaku _sv pipeline)."""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _cell_value(v):
    """Nilai sel utk payload BARIS. Pertahankan angka/boolean supaya Excel hasil
    rakit tetap bertipe numerik saat dialirkan ke backend (mis. skor). Sisanya
    (datetime/Decimal/dll) -> string. WAJIB JSON-serializable."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        return v
    return _cell_to_str(v)


def sheet_to_rows(ws, id_headers=None):
    """Parse 1 worksheet -> (headers_list, rows). rows = list (biz_key, payload).
    payload = dict {header: nilai string} untuk semua kolom ber-header. biz_key
    diambil dari kolom identitas bila ada; bila kosong -> None (kunci sintetis)."""
    info = H.read_sheet(ws)
    headers_by_col = {}
    for name, col in info["headers"].items():
        headers_by_col[col] = name
    ordered_cols = sorted(headers_by_col.keys())
    headers = [headers_by_col[c] for c in ordered_cols]
    id_col = H._find_header(info["headers"], id_headers or _ROW_ID_DEFAULT)
    rows = []
    for rn in sorted(info["rows"].keys()):
        if rn == 1:
            continue
        cells = info["rows"][rn]
        payload = {}
        nonempty = False
        for c in ordered_cols:
            val = _cell_value(cells.get(c))
            payload[headers_by_col[c]] = val
            if val != "":
                nonempty = True
        if not nonempty:
            continue
        biz_key = _cell_to_str(cells.get(id_col)).strip() if id_col else ""
        rows.append((biz_key if biz_key else None, payload))
    return headers, rows


def workbook_to_sheets(wb, id_headers=None):
    """Parse SEMUA sheet -> (headers_map {sheet:[h]}, sheets_rows {sheet:[(biz_key,payload)]})."""
    headers_map = {}
    sheets_rows = {}
    for name in wb.sheetnames:
        heads, rows = sheet_to_rows(wb[name], id_headers)
        headers_map[name] = heads
        sheets_rows[name] = rows
    return headers_map, sheets_rows


def assemble_step_xlsx(conn, dataset_id, step):
    """Rakit bytes XLSX dari step_row (+ overlay step_edit). None bila tak ada baris."""
    step = int(step)
    if not pstore.has_rows(conn, dataset_id, step):
        return None
    headers_map = pstore.get_meta_value(conn, dataset_id, _headers_meta_key(step), {}) or {}
    edit_sheet = pstore.get_meta_value(conn, dataset_id, _editsheet_meta_key(step), None)
    edits = pstore.list_edits(conn, dataset_id, step)
    sheets = []
    for sheet in pstore.list_step_sheets(conn, dataset_id, step):
        srows = pstore.list_sheet_rows(conn, dataset_id, step, sheet)
        apply_edits = (edit_sheet is None) or (sheet == edit_sheet)
        heads = list(headers_map.get(sheet) or [])
        if not heads:
            seen = set()
            for row in srows:
                for k in row["data"].keys():
                    if k not in seen:
                        seen.add(k)
                        heads.append(k)
        if apply_edits:
            for row in srows:
                ov = edits.get(row["biz_key"])
                if isinstance(ov, dict):
                    for k in ov.keys():
                        if k not in heads:
                            heads.append(k)
        aoa = [heads]
        for row in srows:
            data = dict(row["data"])
            if apply_edits:
                ov = edits.get(row["biz_key"])
                if isinstance(ov, dict):
                    data.update(ov)
            aoa.append([data.get(h, "") for h in heads])
        sheets.append({"name": sheet, "rows": aoa})
    if not sheets:
        return None
    return H.xlsx_build(sheets)


def _write_disk_cache(cfg, run, n, ext, b):
    if b is None:
        return
    fname = "step%s.%s" % (n, ext)
    try:
        with open(os.path.join(H.run_dir(cfg, run), fname), "wb") as f:
            f.write(b)
    except Exception:
        pass


def save_step_rows(cfg, run, n, sheets_rows, headers_map, ext, download_name, summary,
                   edit_sheet=None):
    """Simpan hasil step BERBASIS BARIS (bukan blob). sheets_rows: {sheet:[(biz_key,payload)]};
    headers_map: {sheet:[headers]}. Simpan baris + urutan header (meta) + record_step
    (metadata, blob NULL), lalu tulis cache disk hasil rakit. Kembalikan dict state step."""
    mime = H.mime_for_ext(ext)
    conn = H._store()
    try:
        ds = H._resolve_dataset(conn, run, create=True)
        did = ds["id"]
        pstore.replace_step_rows(conn, did, int(n), sheets_rows)
        pstore.set_meta_value(conn, did, _headers_meta_key(n), headers_map or {})
        if edit_sheet is not None:
            pstore.set_meta_value(conn, did, _editsheet_meta_key(n), edit_sheet)
        pstore.record_step(conn, did, int(n), ext, download_name, mime, summary)
        b = assemble_step_xlsx(conn, did, int(n))
    finally:
        conn.close()
    _write_disk_cache(cfg, run, n, ext, b)
    return {
        "status": "done",
        "file": "step%s.%s" % (n, ext),
        "name": download_name,
        "ext": ext,
        "mime": mime,
        "size": (len(b) if b is not None else 0),
        "summary": summary,
        "at": _dt.datetime.now().isoformat(),
    }


def save_step_from_xlsx(cfg, run, n, xlsx_bytes, ext, download_name, summary,
                        id_headers=None, edit_sheet=None):
    """Parse Excel hasil model -> baris (semua sheet), lalu simpan via save_step_rows."""
    wb = H._wb_from_bytes(xlsx_bytes)
    headers_map, sheets_rows = workbook_to_sheets(wb, id_headers)
    return save_step_rows(cfg, run, n, sheets_rows, headers_map, ext, download_name,
                          summary, edit_sheet=edit_sheet)


def save_step_edits(cfg, run, n, items):
    """Simpan editan analis (Step 6 & 9) ke step_edit (kunci bisnis stabil, upsert),
    lalu segarkan cache disk hasil rakit. items: list (biz_key, payload_dict)."""
    b = None
    ext = "xlsx"
    conn = H._store()
    try:
        ds = H._resolve_dataset(conn, run, create=True)
        did = ds["id"]
        pstore.upsert_edits(conn, did, int(n), items)
        meta = pstore.get_artifact_meta(conn, did, int(n))
        if meta and meta.get("ext"):
            ext = meta.get("ext")
        b = assemble_step_xlsx(conn, did, int(n))
    finally:
        conn.close()
    _write_disk_cache(cfg, run, n, ext, b)
    return b


def step_sheet(cfg, run, n, sheet, overlay_edits=True):
    """{headers:[...], rows:[{biz_key,row_index,data}]} untuk satu sheet step n.
    data = dict {header: value}; editan step_edit di-overlay bila overlay_edits."""
    conn = H._store()
    try:
        ds = H._resolve_dataset(conn, run, create=False)
        if not ds:
            return {"headers": [], "rows": []}
        did = ds["id"]
        headers_map = pstore.get_meta_value(conn, did, _headers_meta_key(n), {}) or {}
        srows = pstore.list_sheet_rows(conn, did, int(n), sheet)
        edits = pstore.list_edits(conn, did, int(n)) if overlay_edits else {}
    finally:
        conn.close()
    heads = list(headers_map.get(sheet) or [])
    if not heads:
        seen = set()
        for row in srows:
            for k in row["data"].keys():
                if k not in seen:
                    seen.add(k)
                    heads.append(k)
    out_rows = []
    for row in srows:
        data = dict(row["data"])
        ov = edits.get(row["biz_key"])
        if overlay_edits and isinstance(ov, dict):
            data.update(ov)
            for k in ov.keys():
                if k not in heads:
                    heads.append(k)
        out_rows.append({"biz_key": row["biz_key"], "row_index": row["row_index"], "data": data})
    return {"headers": heads, "rows": out_rows}


def step_has_rows(cfg, run, n):
    conn = H._store()
    try:
        ds = H._resolve_dataset(conn, run, create=False)
        if not ds:
            return False
        return pstore.has_rows(conn, ds["id"], int(n))
    finally:
        conn.close()


def migrate_blob_to_rows(cfg, run, n, id_headers=None, edit_sheet=None):
    """Migrasi lazy: bila step n masih menyimpan blob Excel lama TAPI belum punya baris,
    parse blob -> baris + header (meta), lalu null-kan blob (record_step). Idempoten."""
    conn = H._store()
    try:
        ds = H._resolve_dataset(conn, run, create=False)
        if not ds:
            return False
        did = ds["id"]
        if pstore.has_rows(conn, did, int(n)):
            return False
        meta = pstore.get_artifact_meta(conn, did, int(n))
        if not meta:
            return False
        b = pstore.get_artifact_bytes(conn, did, int(n))
        if b is None:
            return False
        try:
            wb = H._wb_from_bytes(b)
        except Exception:
            return False
        headers_map, sheets_rows = workbook_to_sheets(wb, id_headers)
        pstore.replace_step_rows(conn, did, int(n), sheets_rows)
        pstore.set_meta_value(conn, did, _headers_meta_key(n), headers_map)
        if edit_sheet is not None:
            pstore.set_meta_value(conn, did, _editsheet_meta_key(n), edit_sheet)
        pstore.record_step(conn, did, int(n), meta.get("ext") or "xlsx",
                           meta.get("name") or ("step%d.xlsx" % int(n)),
                           meta.get("mime") or XLSX_MIME, meta.get("summary") or {})
        return True
    finally:
        conn.close()
