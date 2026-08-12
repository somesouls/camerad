# -*- coding: utf-8 -*-
"""step9_patch.py — Perbaikan Step 9 (step9_save) pipeline Analisis Dialogflow.

MASALAH (kumat):
- Frontend (templates/tools.html -> saveStep9) mengirim `edits` sebagai OBJEK
  map {"<row>": "<Intent Seharusnya>"} berisi HANYA baris yang diedit.
- Backend lama (pipeline_routes.step9_save) menolak apa pun yang bukan LIST
  sehingga SELALU melempar "Data edit tidak valid.".
- Akibatnya sheet "Analisis MKTA" tak pernah ditulis, PUTUSAN/ALASAN dari Step 8
  tak terbawa, dan laporan Step 10 rusak.

SOLUSI:
- Ganti step9_save agar menerima DUA format (objek map & list objek),
  memetakan nilai string -> kolom "Intent Seharusnya" (manual),
  tetap membawa PUTUSAN/ALASAN dari sheet "QA Conf MKTA" (hasil Step 8),
  serta mempertahankan koreksi lama utk baris yang tidak diedit.

PEMASANGAN: cukup `import step9_patch` SETELAH pipeline_routes selesai diimpor
(lihat web_app.py). dispatch() memakai late-binding global sehingga otomatis
memakai versi ini. File pipeline_routes.py TIDAK diubah.
"""
import json
import pipeline_routes as pr


def step9_save(cfg, ctx):
    edits_raw = ctx.P("edits", "")
    try:
        edits = json.loads(edits_raw) if edits_raw not in (None, "") else {}
    except Exception:
        edits = None
    # Terima DUA format kiriman frontend:
    #  - objek map {"<row>": "<Intent Seharusnya>"}  (format tools.html saat ini)
    #  - list objek [{row, llm, manual|seharusnya, catatan}]  (kompatibel mundur)
    # Nilai None = "jangan ubah / pakai nilai lama".
    edit_map = {}
    if isinstance(edits, dict):
        for k, v in edits.items():
            try:
                rn = int(k)
            except Exception:
                continue
            if isinstance(v, dict):
                edit_map[rn] = {
                    "llm": v.get("llm"),
                    "manual": v.get("manual", v.get("seharusnya")),
                    "catatan": v.get("catatan"),
                }
            else:
                edit_map[rn] = {"manual": ("" if v is None else str(v))}
    elif isinstance(edits, list):
        for e in edits:
            if not isinstance(e, dict):
                continue
            try:
                rn = int(e.get("row"))
            except Exception:
                continue
            edit_map[rn] = {
                "llm": e.get("llm"),
                "manual": e.get("manual", e.get("seharusnya")),
                "catatan": e.get("catatan"),
            }
    else:
        raise Exception("Data edit tidak valid.")
    try:
        threshold = float(ctx.P("threshold", "0.6"))
    except Exception:
        threshold = 0.6
    b = pr.step9_base_bytes(cfg, ctx)
    wb = pr._wb_from_bytes(b)
    if "QA Conf MKTA" not in wb.sheetnames:
        raise Exception('Sheet "QA Conf MKTA" tidak ada.')
    qa = pr.read_sheet(wb["QA Conf MKTA"])
    H = qa["headers"]
    col_id = pr._find_header(H, ["ID trace", "ID Trace"])
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
    # Usulan intent dari LLM (bila backend Step 8 menuliskannya ke QA Conf MKTA).
    col_llm_qa = pr._find_header(H, ["Intent Seharusnya (LLM)", "INTENT SEHARUSNYA", "Intent Seharusnya LLM"])
    # Pertahankan koreksi lama (LLM/Manual/Catatan) utk baris yg TIDAK diedit.
    prior = {}
    if "Analisis MKTA" in wb.sheetnames:
        am = pr.read_sheet(wb["Analisis MKTA"])
        AH = am["headers"]
        p_id = pr._find_header(AH, ["ID trace", "ID Trace"])
        p_user = pr._find_header(AH, ["user phrase", "User Phrase", "Pertanyaan User"])
        p_llm = pr._find_header(AH, ["Intent Seharusnya (LLM)", "INTENT SEHARUSNYA"])
        p_man = pr._find_header(AH, ["Intent Seharusnya", "Intent Seharusnya (Manual)"])
        p_cat = pr._find_header(AH, ["Catatan", "CATATAN"])
        for rn in sorted(am["rows"].keys()):
            if rn == 1:
                continue
            cells = am["rows"][rn]
            key = pr._sv(cells, p_id) + "||" + pr._sv(cells, p_user)
            prior[key] = {
                "llm": pr._sv(cells, p_llm),
                "manual": pr._sv(cells, p_man),
                "catatan": pr._sv(cells, p_cat),
            }
    header = ["ID trace", "user phrase", "bot response", "intent name", "Skor Pemrosesan Bahasa",
             "PUTUSAN", "ALASAN", "Intent Seharusnya (LLM)", "Intent Seharusnya", "Catatan",
             "waktu interaksi", "lang", "insertId", "score"]
    aoa = [header]
    baris = 0
    for rn in sorted(qa["rows"].keys()):
        if rn == 1:
            continue
        cells = qa["rows"][rn]
        sc = pr._sv(cells, col_score)
        if not (pr._is_numeric(sc) and float(sc) < threshold):
            continue
        key = pr._sv(cells, col_id) + "||" + pr._sv(cells, col_user)
        old = prior.get(key, {})
        llm = old.get("llm", "")
        manual = old.get("manual", "")
        catatan = old.get("catatan", "")
        if (not llm) and col_llm_qa:
            llm = pr._sv(cells, col_llm_qa)
        e = edit_map.get(rn)
        if e is not None:
            if e.get("llm") is not None:
                llm = e.get("llm")
            if e.get("manual") is not None:
                manual = e.get("manual")
            if e.get("catatan") is not None:
                catatan = e.get("catatan")
        aoa.append([
            pr._sv(cells, col_id), pr._sv(cells, col_user), pr._sv(cells, col_bot), pr._sv(cells, col_intent),
            (float(sc) if pr._is_numeric(sc) else sc), pr._sv(cells, col_put), pr._sv(cells, col_alasan),
            llm, manual, catatan, pr._sv(cells, col_waktu), pr._sv(cells, col_lang),
            pr._sv(cells, col_ins), pr._sv(cells, col_scr),
        ])
        baris += 1
    out_bytes = pr.xlsx_upsert_sheet(b, "Analisis MKTA", aoa)
    summary = {"status": "Selesai", "baris_analisis": baris, "threshold": threshold,
               "catatan": 'Sheet "Analisis MKTA" diperbarui.'}
    data = pr.save_artifact(cfg, ctx.run, 9, "xlsx", out_bytes, "hasil_analisis_manual_mkta.xlsx", summary)
    return {"step": 9, "artifact": data, "baris": baris}


# Terapkan monkey-patch: dispatch() di pipeline_routes memakai late-binding
# terhadap global `step9_save`, jadi mengganti atribut modul sudah cukup.
pr.step9_save = step9_save
print("[step9_patch] step9_save diperbaiki (terima objek map + bawa data Step 8).", flush=True)
