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
  beda). Akibatnya `r.qa` selalu undefined -> SEMUA baris tersaring keluar.

MASALAH C (AMBANG — baru): step9_load/step9_save dulu default ke 0.6 sehingga
data yang tampil di Step 9 TIDAK mengikuti ambang yang dipilih analis di Step 8
(mis. analis pilih <0.7 tapi Step 9 cuma tampil <0.6). Kini bila form TIDAK
mengirim `threshold`, ambang DIWARISI dari ringkasan Step 8 (state.steps.8.summary
.threshold). step9_load juga MENGEMBALIKAN `threshold` agar frontend bisa menyetel
nilai default filter (#f9qa) sesuai pilihan analis.

MASALAH D (ID trace kosong — baru): kolom "ID trace" pada sheet Analisis MKTA
sering kosong karena `session_id` di log memang kosong. Kini bila kosong, dipakai
`insertId` (id rekaman Google Logging) sebagai ID trace.

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


def _rid(cells, col_id, col_ins):
    """ID trace = id rekaman. Bila 'ID trace' (session_id) kosong, pakai insertId."""
    v = pr._sv(cells, col_id).strip()
    if v == "" and col_ins:
        v = pr._sv(cells, col_ins).strip()
    return v


def step9_save(cfg, ctx):
    edits_raw = ctx.P("edits", "")
    try:
        edits = json.loads(edits_raw) if edits_raw not in (None, "") else {}
    except Exception:
        edits = None
    # Terima DUA format kiriman frontend:
    #  - objek map {"<row>": "<Intent Seharusnya>"}  (format tools.html saat ini)
    #  - list objek [{row, llm, manual|seharusnya, catatan}]  (kompatibel mundur)
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
    threshold = _resolve_threshold(cfg, ctx)
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
        rid = _rid(cells, col_id, col_ins)
        key = rid + "||" + pr._sv(cells, col_user)
        old = prior.get(key, {})
        if not old:
            old = prior.get(pr._sv(cells, col_id) + "||" + pr._sv(cells, col_user), {})
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
            rid, pr._sv(cells, col_user), pr._sv(cells, col_bot), pr._sv(cells, col_intent),
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
    threshold = _resolve_threshold(cfg, ctx)
    wb = pr._wb_from_bytes(b)
    if "QA Conf MKTA" not in wb.sheetnames:
        raise Exception('Sheet "QA Conf MKTA" tidak ada.')
    qa = pr.read_sheet(wb["QA Conf MKTA"])
    H = qa["headers"]
    col_id = pr._find_header(H, ["ID trace", "ID Trace", "IDtrace"])
    col_ins = pr._find_header(H, ["insertId", "InsertId", "InserId"])
    col_user = pr._find_header(H, ["user phrase", "User Phrase", "Pertanyaan User"])
    col_bot = pr._find_header(H, ["bot response", "Bot Response", "Jawaban Bot"])
    col_intent = pr._find_header(H, ["intent name", "Intent Name", "Intent"])
    col_score = pr._find_header(H, ["Skor Pemrosesan Bahasa"])
    col_put = pr._find_header(H, ["PUTUSAN"])
    col_alasan = pr._find_header(H, ["ALASAN", "Alasan"])
    col_df = pr._find_header(H, ["Skor Dialogflow", "Skor DF", "Skor Deteksi", "score", "Score"])
    col_nli = pr._find_header(H, ["Skor NLI", "NLI", "nli"])
    col_prio = pr._find_header(H, ["Prioritas", "Priority"])
    col_kat = pr._find_header(H, ["Kategori Mesin", "Kategori", "Category"])
    col_kand = pr._find_header(H, ["Kandidat", "Terdekat", "Intent Terdekat", "Kandidat Intent", "Intent Kandidat"])
    col_llm_qa = pr._find_header(H, ["Intent Seharusnya (LLM)", "INTENT SEHARUSNYA", "Intent Seharusnya LLM"])
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
        _id = _rid(cells, col_id, col_ins)
        user = pr._sv(cells, col_user)
        old = prior.get(_id + "||" + user, {})
        if not old:
            old = prior.get(pr._sv(cells, col_id) + "||" + user, {})
        seharusnya = old.get("manual", "") or old.get("llm", "")
        if (not seharusnya) and col_llm_qa:
            seharusnya = pr._sv(cells, col_llm_qa)
        rows.append({
            "row": rn,
            "id_trace": _id,
            "pertanyaan": user,
            "qa": sc,
            "df": pr._sv(cells, col_df),
            "nli": pr._sv(cells, col_nli),
            "prioritas": pr._sv(cells, col_prio),
            "kategori": pr._sv(cells, col_kat),
            "kandidat": pr._sv(cells, col_kand),
            "seharusnya": seharusnya,
            "user": user,
            "skor": sc,
            "manual": old.get("manual", ""),
            "bot": pr._sv(cells, col_bot),
            "intent": pr._sv(cells, col_intent),
            "putusan": pr._sv(cells, col_put),
            "alasan": pr._sv(cells, col_alasan),
            "llm": old.get("llm", ""),
            "catatan": old.get("catatan", ""),
        })
    return {"step": 9, "rows": rows, "total": len(rows), "threshold": threshold}


# Terapkan monkey-patch: dispatch() di pipeline_routes memakai late-binding
# terhadap global `step9_save`/`step9_load`, jadi mengganti atribut modul cukup.
pr.step9_save = step9_save
pr.step9_load = step9_load
print("[step9_patch] step9_save + step9_load diperbaiki (objek map, warisi ambang Step 8, ID trace<-insertId, samakan nama field frontend).", flush=True)
