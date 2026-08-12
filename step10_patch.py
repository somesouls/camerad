# -*- coding: utf-8 -*-
"""step10_patch.py — Perbaikan + format baru Laporan Step 10 (LM & Pembaruan).

MASALAH:
- Step 10 lama sering "rusak" karena bergantung penuh pada sheet "Analisis MKTA"
  (yang tak pernah tertulis saat Step 9 gagal). Setelah Step 9 diperbaiki
  (step9_patch.py), Step 10 tetap memakai skema lama: TANPA kolom NAMA PENYUSUN
  dan TANPA TGL Penyusunan, serta hanya membaca MKTA.

FORMAT BARU (sesuai kontrak frontend openModal10/runStep10):
- Menerima parameter POST `penyusun` -> kolom "NAMA PENYUSUN".
- "TGL Penyusunan" = tanggal rekaman/interaksi tiap baris (dari "waktu interaksi").
- Menggabungkan baris TINDAK LANJUT dari Analisis MKTA (Step 9) DAN
  Analisis Fallback (Step 6, bila hasilnya tersedia).
- Sheet + CSV "LM" dan "Pembaruan" tetap ditulis (step10_lm.csv / step10_pembaruan.csv)
  supaya tombol unduh (part=lm / part=pembaruan) tetap berfungsi.
- Mengembalikan {ok, artifact, lm_rows, pembaruan_rows}.

PEMASANGAN: cukup `import step10_patch` SETELAH pipeline_routes selesai diimpor
(lihat web_app.py). dispatch() memakai late-binding global step10_build.
File pipeline_routes.py TIDAK diubah.
"""
import os
import re
import pipeline_routes as pr


def _tgl(waktu):
    """Ambil tanggal (YYYY-MM-DD) dari string waktu interaksi."""
    s = (waktu or "").strip()
    if s == "":
        return ""
    m = re.search(r"(\d{4}-\d{2}-\d{2})", s)
    if m:
        return m.group(1)
    m = re.search(r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", s)
    if m:
        return m.group(1)
    return s[:10]


def _merge_pem(dst, src):
    for intent, items in src.items():
        dst.setdefault(intent, [])
        for phrase, tgl in items:
            if not any(p == phrase for p, _ in dst[intent]):
                dst[intent].append((phrase, tgl))


def _collect_mkta(wb):
    """Baris TINDAK LANJUT dari sheet Analisis MKTA (hasil Step 9)."""
    lm = []
    pem = {}
    if "Analisis MKTA" not in wb.sheetnames:
        return lm, pem
    am = pr.read_sheet(wb["Analisis MKTA"])
    H = am["headers"]
    c_id = pr._find_header(H, ["ID trace", "ID Trace"])
    c_user = pr._find_header(H, ["user phrase", "Pertanyaan User"])
    c_bot = pr._find_header(H, ["bot response", "Jawaban Bot"])
    c_intent = pr._find_header(H, ["intent name", "Intent"])
    c_put = pr._find_header(H, ["PUTUSAN"])
    c_seharusnya = pr._find_header(H, ["Intent Seharusnya", "Intent Seharusnya (Manual)"])
    c_llm = pr._find_header(H, ["Intent Seharusnya (LLM)"])
    c_cat = pr._find_header(H, ["Catatan"])
    c_waktu = pr._find_header(H, ["waktu interaksi", "Waktu Interaksi"])
    for rn in sorted(am["rows"].keys()):
        if rn == 1:
            continue
        cells = am["rows"][rn]
        put = pr._sv(cells, c_put).strip().upper()
        seharusnya = pr._sv(cells, c_seharusnya).strip()
        if seharusnya == "":
            seharusnya = pr._sv(cells, c_llm).strip()
        user = pr._sv(cells, c_user)
        tindak = ("TINDAK LANJUT" in put) or (put in ("SALAH", "TIDAK RELEVAN")) or (seharusnya != "")
        if not tindak:
            continue
        tgl = _tgl(pr._sv(cells, c_waktu))
        lm.append(["MKTA", pr._sv(cells, c_id), user, pr._sv(cells, c_bot),
                   pr._sv(cells, c_intent), pr._sv(cells, c_put), seharusnya,
                   pr._sv(cells, c_cat), tgl])
        if seharusnya != "" and user.strip() != "":
            pem.setdefault(seharusnya, [])
            if not any(p == user for p, _ in pem[seharusnya]):
                pem[seharusnya].append((user, tgl))
    return lm, pem


def _collect_fallback(wb):
    """Baris TINDAK LANJUT dari sheet Analisis Fallback (hasil Step 6, opsional)."""
    lm = []
    pem = {}
    if "Analisis Fallback" not in wb.sheetnames:
        return lm, pem
    af = pr.read_sheet(wb["Analisis Fallback"])
    H = af["headers"]
    c_user = pr._find_header(H, ["Pertanyaan User", "user phrase"])
    c_intent = pr._find_header(H, ["Intent Judgement LLM", "Intent Seharusnya"])
    c_cat = pr._find_header(H, ["Catatan LLM", "Catatan"])
    c_ins = pr._find_header(H, ["ID trace", "ID Trace", "InsertId", "InserId"])
    c_isi = pr._find_header(H, ["Isi Intent", "bot response", "Jawaban Bot"])
    c_waktu = pr._find_header(H, ["waktu interaksi", "Waktu Interaksi"])
    for rn in sorted(af["rows"].keys()):
        if rn == 1:
            continue
        cells = af["rows"][rn]
        seharusnya = pr._sv(cells, c_intent).strip()
        if seharusnya == "":
            continue
        user = pr._sv(cells, c_user)
        tgl = _tgl(pr._sv(cells, c_waktu)) if c_waktu else ""
        lm.append(["Fallback", pr._sv(cells, c_ins), user, pr._sv(cells, c_isi),
                   "", "TINDAK LANJUT", seharusnya, pr._sv(cells, c_cat), tgl])
        if user.strip() != "":
            pem.setdefault(seharusnya, [])
            if not any(p == user for p, _ in pem[seharusnya]):
                pem[seharusnya].append((user, tgl))
    return lm, pem


def step10_build(cfg, ctx):
    penyusun = (ctx.P("penyusun", "") or "").strip()
    state = pr.load_state(cfg, ctx.run)
    s9 = state["steps"].get("9")
    if not s9 or not s9.get("file"):
        raise Exception("Hasil Step 9 belum ada. Jalankan Step 9 dulu.")
    p9 = os.path.join(pr.run_dir(cfg, ctx.run), s9["file"])
    if not os.path.isfile(p9):
        raise Exception("File hasil Step 9 hilang dari server.")
    with open(p9, "rb") as f:
        b9 = f.read()
    wb9 = pr._wb_from_bytes(b9)
    if "Analisis MKTA" not in wb9.sheetnames:
        raise Exception('Sheet "Analisis MKTA" tidak ada. Jalankan Step 9 dulu.')

    lm_data = []
    pem_map = {}

    lm_m, pem_m = _collect_mkta(wb9)
    lm_data.extend(lm_m)
    _merge_pem(pem_map, pem_m)

    # Fallback (Step 6) — opsional & defensif
    s6 = state["steps"].get("6")
    if s6 and s6.get("file"):
        p6 = os.path.join(pr.run_dir(cfg, ctx.run), s6["file"])
        if os.path.isfile(p6):
            try:
                with open(p6, "rb") as f:
                    b6 = f.read()
                wb6 = pr._wb_from_bytes(b6)
                lm_f, pem_f = _collect_fallback(wb6)
                lm_data.extend(lm_f)
                _merge_pem(pem_map, pem_f)
            except Exception:
                pass

    lm_header = ["Sumber", "ID trace", "user phrase", "bot response", "intent name",
                 "PUTUSAN", "Intent Seharusnya", "Catatan", "TGL Penyusunan"]
    lm_rows = [lm_header] + lm_data

    pem_header = ["Intent Seharusnya", "Training Phrase Baru", "NAMA PENYUSUN", "TGL Penyusunan"]
    pem_rows = [pem_header]
    for intent in sorted(pem_map.keys()):
        for phrase, tgl in pem_map[intent]:
            pem_rows.append([intent, phrase, penyusun, tgl])

    out_bytes = pr.xlsx_upsert_sheet(b9, "LM", lm_rows)
    out_bytes = pr.xlsx_upsert_sheet(out_bytes, "Pembaruan", pem_rows)
    d = pr.run_dir(cfg, ctx.run)
    with open(os.path.join(d, "step10_lm.csv"), "wb") as f:
        f.write(pr._csv_bytes(lm_rows))
    with open(os.path.join(d, "step10_pembaruan.csv"), "wb") as f:
        f.write(pr._csv_bytes(pem_rows))
    summary = {"status": "Selesai", "penyusun": (penyusun or "(kosong)"),
               "baris_LM": len(lm_rows) - 1, "baris_Pembaruan": len(pem_rows) - 1,
               "catatan": "Excel + CSV LM + CSV Pembaruan siap diunduh (format baru: NAMA PENYUSUN & TGL Penyusunan; sumber Fallback + MKTA)."}
    data = pr.save_artifact(cfg, ctx.run, 10, "xlsx", out_bytes, "laporan_LM_dan_pembaruan.xlsx", summary)
    return {"step": 10, "artifact": data, "lm_rows": len(lm_rows) - 1, "pembaruan_rows": len(pem_rows) - 1}


# Terapkan monkey-patch (late-binding global di dispatch()).
pr.step10_build = step10_build
print("[step10_patch] step10_build diperbarui (NAMA PENYUSUN + TGL Penyusunan, gabung Fallback+MKTA).", flush=True)
