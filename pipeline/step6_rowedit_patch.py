# -*- coding: utf-8 -*-
"""step6_rowedit_patch.py — Step 6 (Analisis Manual Fallback): editan analis
PERSIST + kolom STATUS. Pola sama seperti Step 9 (step_edit, kunci bisnis stabil).

MASALAH (dilaporkan analis):
- Dropdown "Intent Judgement LLM" punya opsi "Kosongkan". Setelah dipilih lalu
  Simpan, STATUS jadi kosong — TAPI saat modal dibuka ulang, nilai lama
  (rekomendasi LLM) MUNCUL LAGI: "kosongkan" tidak benar-benar tersimpan.
  Memilih intent (rekomendasi/pencarian) lalu simpan -> tersimpan benar.
  Jadi nilai KOSONG jatuh balik ke nilai lama saat dimuat ulang (loader lama
  fallback ke rekomendasi bila sel intent kosong). Sama seperti bug Step 9 dulu
  ('manual or llm'); Step 9 sudah diperbaiki lewat step_edit.

PERBAIKAN (non-invasif; membungkus step6_save & step6_load asli):
- SIMPAN: setelah step6_save asli (menulis step6_source.xlsx + step_row via
  store_rows_patch), UPSERT editan analis ke step_edit(6) DIKUNCI InsertId
  (kunci bisnis stabil, sama dg rowstore.sheet_to_rows). Payload per baris:
  {<kolom intent>: intent, "STATUS": "TINDAK LANJUT"/"KOSONG"}. Nilai intent
  diambil dari payload frontend (bukan menebak sel) -> aman. step_edit ini
  OVERLAY otoritatif: "kosongkan" persist; kolom STATUS otomatis muncul di Excel
  "Analisis Fallback" (assemble menambah kolom overlay yg belum ada di header).
- MUAT: setelah step6_load asli, TIMPA r.intent tiap baris dari step_edit(6)
  (via InsertId) -> nilai analis (termasuk KOSONG) yg tampil, MELEWATI fallback
  loader lama.

Konsisten dg arsitektur "DB sumber kebenaran, Excel dirakit on-demand": editan
analis kini hidup di pipeline_store.db (step_edit), bukan cuma blob Excel.

PEMASANGAN: chain-import dari store_rows_patch (SETELAH lazy_excel_patch) supaya
rowstore.save_step_edits versi lazy dipakai (hapus cache -> rakit ulang saat
dibuka). step6_patch/step6_idtrace_patch/judge_audit_patch membungkus step6_load
versi ini (dipasang setelahnya) -> sinyal/id_trace/acuan tetap jalan.
"""
import json
import os

import pipeline.routes as pr
from pipeline import rowstore
from pipeline.helpers import run_dir, _wb_from_bytes, read_sheet, _sv, _find_header

STEP = 6
SHEET = "Analisis Fallback"
COL_STATUS = "STATUS"
ST_TL = "TINDAK LANJUT"
ST_KO = "KOSONG"

# Kandidat header kolom identitas (kunci bisnis) — SAMAKAN urutan dg rowstore.
ID_HEADERS = ["insertId", "InsertId", "InserId", "ID Rekaman", "ID Percakapan",
              "id_rekaman", "ID trace", "ID Trace", "IDtrace"]
# Kandidat nama header kolom intent (hasil LLM) pd sheet Analisis Fallback.
INTENT_HEADERS = ["Intent Judgement LLM", "Intent Judgment LLM", "Intent Judgement",
                  "Intent Rekomendasi LLM", "Rekomendasi Intent LLM", "Rekomendasi Intent",
                  "Intent Seharusnya", "INTENT SEHARUSNYA", "Intent"]

_orig_step6_save = getattr(pr, "step6_save", None)
_orig_step6_load = getattr(pr, "step6_load", None)


def _actual_header(headers, candidates):
    """Nama header PERSIS yg ada di sheet (match case-insensitive) atau None."""
    low = {}
    for k in (headers or {}).keys():
        low[str(k).strip().lower()] = k
    for c in candidates:
        k = low.get(str(c).strip().lower())
        if k is not None:
            return k
    return None


def _source_info(cfg, ctx):
    """Baca step6_source.xlsx (sheet Analisis Fallback) ->
    (rn2bk {nomor_baris: InsertId}, intent_header). Fail-open."""
    rn2bk = {}
    intent_header = INTENT_HEADERS[0]
    try:
        p = os.path.join(run_dir(cfg, ctx.run), "step6_source.xlsx")
        if not os.path.isfile(p):
            return rn2bk, intent_header
        with open(p, "rb") as f:
            b = f.read()
        wb = _wb_from_bytes(b)
        if SHEET not in wb.sheetnames:
            return rn2bk, intent_header
        sh = read_sheet(wb[SHEET])
        H = sh["headers"]
        intent_header = _actual_header(H, INTENT_HEADERS) or INTENT_HEADERS[0]
        c_ins = _find_header(H, ID_HEADERS)
        if c_ins:
            for rn, cells in sh["rows"].items():
                if rn == 1:
                    continue
                ins = _sv(cells, c_ins).strip()
                if ins:
                    rn2bk[rn] = ins
    except Exception:
        pass
    return rn2bk, intent_header


def _parse_edits(ctx):
    """List [{row, intent, isi}] dari payload frontend (ctx.P('edits'))."""
    try:
        raw = ctx.P("edits", "") or ""
    except Exception:
        raw = ""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except Exception:
        return []
    if isinstance(data, dict):
        out = []
        for k, v in data.items():
            if isinstance(v, dict):
                d = dict(v)
                d.setdefault("row", k)
                out.append(d)
            else:
                out.append({"row": k, "intent": v})
        return out
    if isinstance(data, list):
        return [e for e in data if isinstance(e, dict)]
    return []


def step6_save(cfg, ctx):
    # 1) Simpan spt biasa (step6_source.xlsx + step_row via store_rows_patch).
    res = _orig_step6_save(cfg, ctx) if callable(_orig_step6_save) else None
    # 2) UPSERT editan analis -> step_edit(6): kunci InsertId + kolom STATUS.
    try:
        edits = _parse_edits(ctx)
        if edits:
            rn2bk, intent_header = _source_info(cfg, ctx)
            items = []
            for e in edits:
                rn = e.get("row")
                bk = rn2bk.get(rn)
                if bk is None:
                    try:
                        bk = rn2bk.get(int(rn))
                    except Exception:
                        bk = None
                if not bk:
                    continue
                intent = e.get("intent") or ""
                if isinstance(intent, str):
                    intent = intent.strip()
                status = ST_TL if intent not in (None, "") else ST_KO
                items.append((str(bk), {intent_header: intent, COL_STATUS: status}))
            if items:
                rowstore.save_step_edits(cfg, ctx.run, STEP, items)
    except Exception as e:
        try:
            print("[step6_rowedit_patch] simpan step_edit gagal (fail-open): %r" % e, flush=True)
        except Exception:
            pass
    return res


def step6_load(cfg, ctx):
    res = _orig_step6_load(cfg, ctx) if callable(_orig_step6_load) else {"step": STEP, "rows": []}
    try:
        rows = (res or {}).get("rows") or []
        if rows:
            rn2bk, intent_header = _source_info(cfg, ctx)
            stored = rowstore.step_sheet(cfg, ctx.run, STEP, SHEET)
            ov = {}
            for row in stored.get("rows", []):
                ov[row.get("biz_key")] = row.get("data") or {}
            for r in rows:
                bk = rn2bk.get(r.get("row"))
                if not bk:
                    continue
                d = ov.get(bk)
                if isinstance(d, dict) and (intent_header in d):
                    r["intent"] = d.get(intent_header) or ""
    except Exception:
        pass
    return res


if callable(_orig_step6_save):
    pr.step6_save = step6_save
if callable(_orig_step6_load):
    pr.step6_load = step6_load

try:
    print("[step6_rowedit_patch] Step 6 persist fix: editan -> step_edit (InsertId) + kolom STATUS; kosongkan tersimpan; UI & Excel dari DB.", flush=True)
except Exception:
    pass
