#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fix_step9_save.py — Perbaiki bug Step 9 "Data edit tidak valid".

Akar masalah:
  Frontend (tools.html -> saveStep9) mengirim field `edits` sebagai OBJEK/map
  {"<row>": "<Intent Seharusnya>"} , sedangkan backend lama (step9_save) hanya
  menerima LIST dan langsung menolak dengan Exception("Data edit tidak valid.").
  Selain itu backend lama membaca field llm/manual/catatan (tidak dikirim UI)
  sehingga koreksi manual tak tersimpan, dan menimpa baris lain jadi kosong.

Perbaikan step9_save:
  * Menerima objek map {row: nilai}  DAN  list objek  (kompatibel mundur).
  * Nilai string dipetakan ke kolom "Intent Seharusnya" (manual).
  * Mempertahankan nilai LLM / Manual / Catatan lama utk baris yang TIDAK diedit
    (mencegah data hilang saat sheet Analisis MKTA dibangun ulang).

Pakai:
    python fix_step9_save.py pipeline_routes.py
"""
import sys, os, py_compile

NEW_FUNC = '''def step9_save(cfg, ctx):
    edits_raw = ctx.P("edits", "")
    try:
        edits = json.loads(edits_raw) if str(edits_raw).strip() != "" else []
    except Exception:
        edits = None
    if edits is None:
        raise Exception("Data edit tidak valid.")

    # Normalisasi edits: terima OBJEK map {row: nilai/obj} (dikirim frontend) MAUPUN
    # list objek {row, llm, manual/seharusnya, catatan} (kompatibel mundur).
    edit_map = {}
    if isinstance(edits, dict):
        for k, v in edits.items():
            try:
                rn = int(k)
            except Exception:
                continue
            if isinstance(v, dict):
                edit_map[rn] = v
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
            edit_map[rn] = e
    else:
        raise Exception("Data edit tidak valid.")

    try:
        threshold = float(ctx.P("threshold", "0.6"))
    except Exception:
        threshold = 0.6
    b = step9_base_bytes(cfg, ctx)
    wb = _wb_from_bytes(b)
    if "QA Conf MKTA" not in wb.sheetnames:
        raise Exception('Sheet "QA Conf MKTA" tidak ada.')
    qa = read_sheet(wb["QA Conf MKTA"])
    H = qa["headers"]
    col_id = _find_header(H, ["ID trace", "ID Trace"])
    col_user = _find_header(H, ["user phrase", "User Phrase", "Pertanyaan User"])
    col_bot = _find_header(H, ["bot response", "Bot Response", "Jawaban Bot"])
    col_intent = _find_header(H, ["intent name", "Intent Name", "Intent"])
    col_score = _find_header(H, ["Skor Pemrosesan Bahasa"])
    col_put = _find_header(H, ["PUTUSAN"])
    col_alasan = _find_header(H, ["ALASAN", "Alasan"])
    col_waktu = _find_header(H, ["waktu interaksi", "Waktu Interaksi"])
    col_lang = _find_header(H, ["lang", "Lang"])
    col_ins = _find_header(H, ["insertId", "InsertId", "InserId"])
    col_scr = _find_header(H, ["score", "Score"])

    # Pertahankan nilai analisis sebelumnya (LLM/Manual/Catatan) utk baris yg
    # tidak diedit, keyed by ID trace + user phrase (sama seperti step9_load).
    prior = {}
    if "Analisis MKTA" in wb.sheetnames:
        am = read_sheet(wb["Analisis MKTA"])
        AH = am["headers"]
        p_id = _find_header(AH, ["ID trace", "ID Trace"])
        p_user = _find_header(AH, ["user phrase", "User Phrase", "Pertanyaan User"])
        p_llm = _find_header(AH, ["Intent Seharusnya (LLM)", "INTENT SEHARUSNYA"])
        p_man = _find_header(AH, ["Intent Seharusnya", "Intent Seharusnya (Manual)"])
        p_cat = _find_header(AH, ["Catatan", "CATATAN"])
        for rn in sorted(am["rows"].keys()):
            if rn == 1:
                continue
            cells = am["rows"][rn]
            key = _sv(cells, p_id) + "||" + _sv(cells, p_user)
            prior[key] = {
                "llm": _sv(cells, p_llm),
                "manual": _sv(cells, p_man),
                "catatan": _sv(cells, p_cat),
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
        sc = _sv(cells, col_score)
        if not (_is_numeric(sc) and float(sc) < threshold):
            continue
        _id = _sv(cells, col_id)
        user = _sv(cells, col_user)
        pr = prior.get(_id + "||" + user, {})
        e = edit_map.get(rn, {})
        # 'seharusnya' (alias frontend) -> kolom "Intent Seharusnya" (manual)
        if "manual" in e:
            manual = e.get("manual", "")
        elif "seharusnya" in e:
            manual = e.get("seharusnya", "")
        else:
            manual = pr.get("manual", "")
        llm = e.get("llm", pr.get("llm", ""))
        catatan = e.get("catatan", pr.get("catatan", ""))
        aoa.append([
            _id, user, _sv(cells, col_bot), _sv(cells, col_intent),
            (float(sc) if _is_numeric(sc) else sc), _sv(cells, col_put), _sv(cells, col_alasan),
            llm, manual, catatan, _sv(cells, col_waktu), _sv(cells, col_lang),
            _sv(cells, col_ins), _sv(cells, col_scr),
        ])
        baris += 1
    out_bytes = xlsx_upsert_sheet(b, "Analisis MKTA", aoa)
    summary = {"status": "Selesai", "baris_analisis": baris, "threshold": threshold,
               "catatan": 'Sheet "Analisis MKTA" diperbarui.'}
    data = save_artifact(cfg, ctx.run, 9, "xlsx", out_bytes, "hasil_analisis_manual_mkta.xlsx", summary)
    return {"step": 9, "artifact": data, "baris": baris}
'''


def patch(path):
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    start_token = "def step9_save(cfg, ctx):"
    if start_token not in src:
        raise SystemExit("[GAGAL] Fungsi step9_save tidak ditemukan di %s" % path)
    start = src.index(start_token)
    marker = "# =============================================================\n# STEP 10"
    if marker not in src:
        raise SystemExit("[GAGAL] Penanda blok STEP 10 tidak ditemukan; file mungkin sudah berubah.")
    end = src.index(marker, start)
    if "terima OBJEK map" in src[start:end]:
        print("[INFO] step9_save sepertinya sudah dipatch. Menimpa ulang dgn versi terbaru.")
    new_src = src[:start] + NEW_FUNC.strip("\n") + "\n\n\n" + src[end:]
    if not os.path.exists(path + ".bak"):
        with open(path + ".bak", "w", encoding="utf-8") as f:
            f.write(src)
        print("[OK] Backup dibuat: %s.bak" % path)
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_src)
    print("[OK] step9_save diganti di %s" % path)
    py_compile.compile(path, doraise=True)
    print("[OK] py_compile sukses — tidak ada syntax error.")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "pipeline_routes.py"
    patch(target)
    print("\nSelesai. Restart server (uvicorn web_app:app ...) lalu coba Simpan Perubahan di Step 9.")
