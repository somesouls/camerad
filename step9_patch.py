# -*- coding: utf-8 -*-
"""step9_patch.py — Perbaikan Step 9 (step9_save + step9_load) pipeline Analisis Dialogflow.

MASALAH A (SIMPAN — "Data edit tidak valid"):
- Frontend (templates/tools.html -> saveStep9) mengirim `edits` sebagai OBJEK
  map {"<row>": "<Intent Seharusnya>"} berisi HANYA baris yang diedit.
- Backend lama (pipeline_routes.step9_save) menolak apa pun yang bukan LIST
  sehingga SELALU melempar "Data edit tidak valid.".

MASALAH B (TAMPIL — tabel Step 9 kosong):
- Frontend renderStep9() menyaring baris dengan `r.qa < ambang` (default 0.5)
  dan membaca field `pertanyaan`, `seharusnya`, `kategori`, `df`, `nli`,
  `prioritas`, `kandidat`.
- Backend lama step9_load justru mengembalikan `user`, `skor`, `manual` (nama
  beda). Akibatnya `r.qa` selalu undefined -> SEMUA baris tersaring keluar ->
  tabel tampak kosong walau data sebenarnya termuat. Restart tak menolong
  karena ini murni ketidakcocokan nama field.

SOLUSI:
- step9_save: terima DUA format (objek map & list objek), petakan string ->
  "Intent Seharusnya" (manual), tetap bawa PUTUSAN/ALASAN dari "QA Conf MKTA"
  (hasil Step 8), pertahankan koreksi lama utk baris yg tak diedit.
- step9_load: kembalikan field dengan nama yang DIBACA frontend
  (qa, pertanyaan, seharusnya, df, nli, kategori, prioritas, kandidat) sambil
  tetap menyertakan nama lama (user/skor/manual) utk kompatibilitas.

PEMASANGAN: cukup `import step9_patch` SETELAH pipeline_routes selesai diimpor
(lihat web_app.py). dispatch() memakai late-binding global sehingga otomatis
memakai versi ini. File pipeline_routes.py TIDAK diubah.
"""
import os
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


def step9_load(cfg, ctx):
    up = ctx.file("xlsx_file")
    if up is not None:
        b = up[0]
        try:
            with open(os.path.join(pr.run_dir(cfg, ctx.run), "step9_source.xlsx"), "wb") as f:
                f.write(b)
        except Exception:
            pass
    else:
        b = pr.step9_base_bytes(cfg, ctx)
    try:
        threshold = float(ctx.P("threshold", "0.6"))
    except Exception:
        threshold = 0.6
    wb = pr._wb_from_bytes(b)
    if "QA Conf MKTA" not in wb.sheetnames:
        raise Exception('Sheet "QA Conf MKTA" tidak ada.')
    qa = pr.read_sheet(wb["QA Conf MKTA"])
    H = qa["headers"]
    col_id = pr._find_header(H, ["ID trace", "ID Trace", "IDtrace"])
    col_user = pr._find_header(H, ["user phrase", "User Phrase", "Pertanyaan User"])
    col_bot = pr._find_header(H, ["bot response", "Bot Response", "Jawaban Bot"])
    col_intent = pr._find_header(H, ["intent name", "Intent Name", "Intent"])
    col_score = pr._find_header(H, ["Skor Pemrosesan Bahasa"])
    col_put = pr._find_header(H, ["PUTUSAN"])
    col_alasan = pr._find_header(H, ["ALASAN", "Alasan"])
    # Kolom tambahan (best-effort; mungkin tak ada di sheet, biarkan kosong).
    col_df = pr._find_header(H, ["Skor Dialogflow", "Skor DF", "Skor Deteksi", "score", "Score"])
    col_nli = pr._find_header(H, ["Skor NLI", "NLI", "nli"])
    col_prio = pr._find_header(H, ["Prioritas", "Priority"])
    col_kat = pr._find_header(H, ["Kategori Mesin", "Kategori", "Category"])
    col_kand = pr._find_header(H, ["Kandidat", "Terdekat", "Intent Terdekat", "Kandidat Intent", "Intent Kandidat"])
    col_llm_qa = pr._find_header(H, ["Intent Seharusnya (LLM)", "INTENT SEHARUSNYA", "Intent Seharusnya LLM"])
    # Koreksi lama dari sheet Analisis MKTA (bila sudah pernah disimpan).
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
    rows = []
    for rn in sorted(qa["rows"].keys()):
        if rn == 1:
            continue
        cells = qa["rows"][rn]
        sc = pr._sv(cells, col_score)
        if not (pr._is_numeric(sc) and float(sc) < threshold):
            continue
        _id = pr._sv(cells, col_id)
        user = pr._sv(cells, col_user)
        key = _id + "||" + user
        old = prior.get(key, {})
        seharusnya = old.get("manual", "") or old.get("llm", "")
        if (not seharusnya) and col_llm_qa:
            seharusnya = pr._sv(cells, col_llm_qa)
        rows.append({
            "row": rn,
            "id_trace": _id,
            # nama field yang DIBACA frontend renderStep9()
            "pertanyaan": user,
            "qa": sc,
            "df": pr._sv(cells, col_df),
            "nli": pr._sv(cells, col_nli),
            "prioritas": pr._sv(cells, col_prio),
            "kategori": pr._sv(cells, col_kat),
            "kandidat": pr._sv(cells, col_kand),
            "seharusnya": seharusnya,
            # nama lama (kompatibilitas mundur)
            "user": user,
            "skor": sc,
            "manual": old.get("manual", ""),
            # umum
            "bot": pr._sv(cells, col_bot),
            "intent": pr._sv(cells, col_intent),
            "putusan": pr._sv(cells, col_put),
            "alasan": pr._sv(cells, col_alasan),
            "llm": old.get("llm", ""),
            "catatan": old.get("catatan", ""),
        })
    return {"step": 9, "rows": rows, "total": len(rows)}


# Terapkan monkey-patch: dispatch() di pipeline_routes memakai late-binding
# terhadap global `step9_save`/`step9_load`, jadi mengganti atribut modul cukup.
pr.step9_save = step9_save
pr.step9_load = step9_load
print("[step9_patch] step9_save + step9_load diperbaiki (objek map, bawa data Step 8, samakan nama field frontend).", flush=True)
