# -*- coding: utf-8 -*-
"""step9_patch.py — Step 9 (Analisis Manual MKTA) BERBASIS BARIS (row-based).

PERBAIKAN INTI ("Intent Seharusnya" tak tersimpan):
- Versi lama MEMBANGUN ULANG sheet "Analisis MKTA" dari nol tiap simpan, dengan
  edit_map DIKUNCI NOMOR BARIS (int(rn)) & prior digabung via 'ID Rekaman||user'.
  Bila kunci meleset (ID Rekaman kosong / nomor baris bergeser), editan analis
  HILANG. Frontend hanya mengirim baris yang diedit -> baris lama rawan lenyap.
- Versi ini menyimpan BARIS deliverable "Analisis MKTA" di step_row (dirakit
  ulang deterministik dari 'QA Conf MKTA' Step 8) DAN menyimpan EDITAN analis
  (Intent Seharusnya / Catatan) TERPISAH di step_edit, DIKUNCI kunci bisnis
  STABIL (insertId -> 'ID Rekaman||user' -> 'r<rownum>'). step_edit di-UPSERT,
  TIDAK pernah dibangun ulang -> editan persist walau baris/urutan berubah.
- r["row"] yang dikirim ke frontend = kunci bisnis; saveStep9 mengirim
  edits[kunci]=Intent Seharusnya, jadi otomatis ter-upsert ke kunci yang benar.

WORKBOOK LENGKAP (perbaikan "Excel Step 9 hanya 1 sheet"):
- step9_load kini IKUT menyimpan SEMUA sheet hulu dari Step 8 (Interaksi ..
  QA Conf MKTA) ke step_row Step 9, lalu meletakkan "Analisis MKTA" TERAKHIR.
  Dengan begitu Excel Step 9 yang dirakit ulang = satu workbook LENGKAP dan
  BERURUTAN (bukan hanya "Analisis MKTA"). Karena Step 10 menumpuk sheet LM/
  Pembaruan di ATAS bytes Step 9, Laporan Utama Step 10 pun otomatis lengkap:
  Interaksi .. QA Conf MKTA + Analisis MKTA + LM .. Data Pembaruan.
- "Analisis MKTA" tetap SATU-SATUNYA edit_sheet (editan analis hanya di-overlay
  ke sheet ini); sheet hulu disalin apa adanya (tanpa overlay editan).

Ambang (threshold): dari form bila ada; jika tidak, warisi ringkasan Step 8.
Sumber data QA: unggahan xlsx (bila ada) atau hasil Step 8 (dirakit dari baris).

PEMASANGAN: `import pipeline.step9_patch` SETELAH pipeline_routes (web_app.py).
dispatch() memakai late-binding global sehingga otomatis memakai versi ini.
Di akhir modul: chain-import store_rows_patch (penyimpanan baris Step 4-8) &
step11_zipfield_patch (perbaikan field ZIP Step 11).
"""
import os
import json
import pipeline.routes as pr
from pipeline import rowstore

# Kandidat nama kolom identitas rekaman (Nomor Rekaman). Urutan = prioritas.
ID_REKAMAN_HEADERS = ["ID Rekaman", "ID Percakapan", "id_rekaman", "ID trace", "ID Trace", "IDtrace"]

# Sheet deliverable Step 9 + kolomnya. "Intent Seharusnya" & "Catatan" = kolom
# EDITAN analis (di-overlay dari step_edit saat rakit Excel / tampil).
EDIT_SHEET = "Analisis MKTA"
HEADER_MKTA = ["ID trace", "user phrase", "bot response", "intent name", "Skor Pemrosesan Bahasa",
               "PUTUSAN", "ALASAN", "Intent Seharusnya (LLM)", "Intent Seharusnya", "Catatan",
               "waktu interaksi", "lang", "insertId", "score"]
COL_MANUAL = "Intent Seharusnya"
COL_CATATAN = "Catatan"
COL_LLM = "Intent Seharusnya (LLM)"


def _step8_threshold(cfg, ctx):
    """Ambang yang dipakai analis di Step 8 (dibaca dari ringkasan state Step 8)."""
    try:
        state = pr.load_state(cfg, ctx.run)
        s8 = state["steps"].get("8") or {}
        sm = s8.get("summary") or {}
        t = sm.get("threshold")
        if t is not None and str(t) != "":
            return float(t)
    except Exception:
        pass
    return 0.6


def _resolve_threshold(cfg, ctx):
    """Pakai threshold eksplisit dari form bila ada; jika tidak, warisi dari Step 8."""
    raw = (ctx.P("threshold", "") or "").strip()
    if raw != "":
        try:
            return float(raw)
        except Exception:
            pass
    return _step8_threshold(cfg, ctx)


def _rid(cells, col_id):
    """ID trace = Nomor Rekaman (kolom 'ID Rekaman'). BUKAN insertId."""
    return pr._sv(cells, col_id).strip()


def _biz_key(ins, rid, user, rn):
    """Kunci bisnis STABIL utk 1 baris QA (dasar persist editan). Prioritas:
    insertId (unik per interaksi) -> 'ID Rekaman||user' -> 'r<rownum>' (darurat)."""
    ins = (ins or "").strip()
    if ins:
        return ins
    rid = (rid or "").strip()
    user = (user or "").strip()
    if rid or user:
        return rid + "||" + user
    return "r%d" % rn


def _base_bytes(cfg, ctx):
    """Sumber data QA utk Step 9: unggahan xlsx (bila ada) atau hasil Step 8
    (dirakit dari baris/blob). Hindari step9_base_bytes agar tak sirkular."""
    up = ctx.file("xlsx_file")
    if up is not None:
        b = up[0]
        try:
            with open(os.path.join(pr.run_dir(cfg, ctx.run), "step9_source.xlsx"), "wb") as f:
                f.write(b)
        except Exception:
            pass
        return b
    b = pr.artifact_bytes(cfg, ctx.run, 8)
    if b is None:
        raise Exception("Step 8 belum tersedia. Jalankan Step 8 (atau unggah file) dulu.")
    return b


def _parse_qa(b, threshold):
    """Parse 'QA Conf MKTA' -> list dict per baris QA berskor < threshold.
    Tiap item berisi biz_key + semua field tampilan/deliverable."""
    wb = pr._wb_from_bytes(b)
    if "QA Conf MKTA" not in wb.sheetnames:
        raise Exception('Sheet "QA Conf MKTA" tidak ada.')
    qa = pr.read_sheet(wb["QA Conf MKTA"])
    H = qa["headers"]
    col_id = pr._find_header(H, ID_REKAMAN_HEADERS)
    col_user = pr._find_header(H, ["user phrase", "User Phrase", "Pertanyaan User"])
    col_bot = pr._find_header(H, ["bot response", "Bot Response", "Jawaban Bot"])
    col_intent = pr._find_header(H, ["intent name", "Intent Name", "Intent"])
    col_score = pr._find_header(H, ["Skor Pemrosesan Bahasa"])
    col_put = pr._find_header(H, ["PUTUSAN"])
    col_alasan = pr._find_header(H, ["ALASAN", "Alasan"])
    col_waktu = pr._find_header(H, ["waktu interaksi", "Waktu Interaksi"])
    col_lang = pr._find_header(H, ["lang", "Lang"])
    col_ins = pr._find_header(H, ["insertId", "InsertId", "InserId"])
    col_scr = pr._find_header(H, ["score", "Score"])
    col_df = pr._find_header(H, ["Skor Dialogflow", "Skor DF", "Skor Deteksi", "score", "Score"])
    col_nli = pr._find_header(H, ["Skor NLI", "NLI", "nli"])
    col_prio = pr._find_header(H, ["Prioritas Tinjau", "Prioritas", "Priority"])
    col_kat = pr._find_header(H, ["Kategori Mesin", "Kategori", "Category"])
    col_kand = pr._find_header(H, ["Kandidat Intent", "Kandidat", "Terdekat", "Intent Terdekat", "Intent Kandidat"])
    col_llm_qa = pr._find_header(H, ["Intent Seharusnya (LLM)", "INTENT SEHARUSNYA", "Intent Seharusnya LLM"])
    out = []
    for rn in sorted(qa["rows"].keys()):
        if rn == 1:
            continue
        cells = qa["rows"][rn]
        sc = pr._sv(cells, col_score)
        if not (pr._is_numeric(sc) and float(sc) < threshold):
            continue
        rid = _rid(cells, col_id)
        user = pr._sv(cells, col_user)
        ins = pr._sv(cells, col_ins)
        out.append({
            "biz_key": _biz_key(ins, rid, user, rn),
            "rid": rid, "user": user, "ins": ins,
            "bot": pr._sv(cells, col_bot), "intent": pr._sv(cells, col_intent),
            "sc": sc, "put": pr._sv(cells, col_put), "alasan": pr._sv(cells, col_alasan),
            "waktu": pr._sv(cells, col_waktu), "lang": pr._sv(cells, col_lang),
            "scr": pr._sv(cells, col_scr), "llm": pr._sv(cells, col_llm_qa),
            "df": pr._sv(cells, col_df), "nli": pr._sv(cells, col_nli),
            "prioritas": pr._sv(cells, col_prio), "kategori": pr._sv(cells, col_kat),
            "kandidat": pr._sv(cells, col_kand),
        })
    return out


def _upstream_sheets(b):
    """Parse SEMUA sheet hulu (Step 8) KECUALI 'Analisis MKTA' -> (sheets_rows,
    headers_map) siap disimpan ke step_row. Sheet hulu (Interaksi .. QA Conf
    MKTA) dibawa apa adanya agar Excel Step 9/10 tetap satu workbook lengkap.
    Fail-open: kembalikan ({}, {}) bila gagal parse."""
    sheets_rows = {}
    headers_map = {}
    try:
        wb_up = pr._wb_from_bytes(b)
        up_headers, up_sheets = rowstore.workbook_to_sheets(wb_up)
        for nm in wb_up.sheetnames:
            if nm == EDIT_SHEET:
                continue  # dirakit ulang fresh (data QA + kolom editan analis)
            sheets_rows[nm] = up_sheets.get(nm, [])
            headers_map[nm] = up_headers.get(nm, [])
    except Exception:
        sheets_rows = {}
        headers_map = {}
    return sheets_rows, headers_map


def step9_load(cfg, ctx):
    threshold = _resolve_threshold(cfg, ctx)
    b = _base_bytes(cfg, ctx)
    items = _parse_qa(b, threshold)
    # 1) (Re)bangun baris deliverable "Analisis MKTA" dari QA (dasar deterministik).
    #    Editan analis TIDAK di sini; tersimpan terpisah di step_edit (kunci bisnis).
    rows_store = []
    for it in items:
        payload = {
            "ID trace": it["rid"], "user phrase": it["user"], "bot response": it["bot"],
            "intent name": it["intent"],
            "Skor Pemrosesan Bahasa": (float(it["sc"]) if pr._is_numeric(it["sc"]) else it["sc"]),
            "PUTUSAN": it["put"], "ALASAN": it["alasan"],
            "Intent Seharusnya (LLM)": it["llm"], "Intent Seharusnya": "", "Catatan": "",
            "waktu interaksi": it["waktu"], "lang": it["lang"],
            "insertId": it["ins"], "score": it["scr"],
        }
        rows_store.append((it["biz_key"], payload))
    # 2) Bawa serta SEMUA sheet hulu (Interaksi .. QA Conf MKTA) supaya workbook
    #    Step 9 (dan Step 10 yang menumpuk di atasnya) LENGKAP & berurutan.
    #    "Analisis MKTA" diletakkan TERAKHIR dan tetap satu-satunya edit_sheet.
    sheets_rows, headers_map = _upstream_sheets(b)
    sheets_rows[EDIT_SHEET] = rows_store
    headers_map[EDIT_SHEET] = list(HEADER_MKTA)
    summary = {"status": "Selesai", "baris_analisis": len(rows_store), "threshold": threshold,
               "catatan": 'Workbook lengkap (Interaksi .. QA Conf MKTA + Analisis MKTA) '
                          'disimpan berbasis baris (row-based).'}
    try:
        rowstore.save_step_rows(cfg, ctx.run, 9, sheets_rows, headers_map, "xlsx",
                                "hasil_analisis_manual_mkta.xlsx", summary, edit_sheet=EDIT_SHEET)
    except Exception:
        # Fail-open: minimal (hanya Analisis MKTA) agar Step 9 tetap bisa dibuka
        # walau parsing/penyimpanan sheet hulu bermasalah.
        rowstore.save_step_rows(cfg, ctx.run, 9, {EDIT_SHEET: rows_store},
                                {EDIT_SHEET: list(HEADER_MKTA)}, "xlsx",
                                "hasil_analisis_manual_mkta.xlsx", summary, edit_sheet=EDIT_SHEET)
    # 3) Overlay editan analis (step_edit) utk tampilan frontend.
    stored = rowstore.step_sheet(cfg, ctx.run, 9, EDIT_SHEET)
    ov = {}
    for r in stored.get("rows", []):
        ov[r["biz_key"]] = r["data"]
    rows = []
    for it in items:
        d = ov.get(it["biz_key"], {})
        manual = d.get(COL_MANUAL, "") or ""
        catatan = d.get(COL_CATATAN, "") or ""
        llm = d.get(COL_LLM, "") or it["llm"]
        seharusnya = manual or llm
        rows.append({
            "row": it["biz_key"], "id_trace": it["rid"], "pertanyaan": it["user"],
            "qa": it["sc"], "df": it["df"], "nli": it["nli"],
            "prioritas": it["prioritas"], "kategori": it["kategori"], "kandidat": it["kandidat"],
            "seharusnya": seharusnya, "user": it["user"], "skor": it["sc"],
            "manual": manual, "bot": it["bot"], "intent": it["intent"],
            "putusan": it["put"], "alasan": it["alasan"], "llm": llm, "catatan": catatan,
        })
    return {"step": 9, "rows": rows, "total": len(rows), "threshold": threshold}


def _payload_from_dict(src):
    """Bangun payload editan dari dict frontend (manual/seharusnya/catatan/llm)."""
    payload = {}
    if ("manual" in src) or ("seharusnya" in src):
        payload[COL_MANUAL] = src.get("manual", src.get("seharusnya")) or ""
    if "catatan" in src:
        payload[COL_CATATAN] = src.get("catatan") or ""
    if "llm" in src:
        payload[COL_LLM] = src.get("llm") or ""
    return payload


def step9_save(cfg, ctx):
    edits_raw = ctx.P("edits", "")
    try:
        edits = json.loads(edits_raw) if edits_raw not in (None, "") else {}
    except Exception:
        edits = None
    items = []
    if isinstance(edits, dict):
        # Bentuk utama frontend: {kunci_bisnis: "Intent Seharusnya"} (baris ter-edit saja).
        for biz_key, v in edits.items():
            if not biz_key:
                continue
            if isinstance(v, dict):
                payload = _payload_from_dict(v)
            else:
                payload = {COL_MANUAL: ("" if v is None else str(v))}
            if payload:
                items.append((str(biz_key), payload))
    elif isinstance(edits, list):
        for e in edits:
            if not isinstance(e, dict):
                continue
            biz_key = e.get("row", e.get("biz_key"))
            if not biz_key:
                continue
            payload = _payload_from_dict(e)
            if payload:
                items.append((str(biz_key), payload))
    else:
        raise Exception("Data edit tidak valid.")
    # UPSERT editan (kunci bisnis stabil). TIDAK membangun ulang baris -> tak ada
    # editan yang hilang. Cache disk & rakitan Excel otomatis di-overlay.
    rowstore.save_step_edits(cfg, ctx.run, 9, items)
    art = None
    try:
        art = pr.load_state(cfg, ctx.run)["steps"].get("9")
    except Exception:
        art = None
    return {"step": 9, "artifact": art, "baris": len(items)}


pr.step9_save = step9_save
pr.step9_load = step9_load
print("[step9_patch] Step 9 row-based: workbook lengkap (sheet hulu + Analisis MKTA) -> step_row; editan (Intent Seharusnya/Catatan) -> step_edit (kunci bisnis stabil, persist).", flush=True)

# Aktifkan penyimpanan baris (row-based) utk Step 4-8. Di-chain di sini karena
# step9_patch di-import setelah pipeline_routes siap (fungsi step sudah ada).
import pipeline.store_rows_patch  # noqa: E402,F401

# Perbaikan Step 11 (field ZIP ke backend: df_zip -> zip_file). Di-chain di sini
# agar diterapkan SETELAH pipeline_routes siap (fungsi step11_update sudah ada).
import pipeline.step11_zipfield_patch  # noqa: E402,F401
