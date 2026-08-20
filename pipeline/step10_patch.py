# -*- coding: utf-8 -*-
"""step10_patch.py — Laporan Step 10 (Rekap LM + LM + Pembaruan).

FITUR:
1) SUMBER GANDA (Bug 2): Step 10 bisa memakai (a) artefak Step 9 di server, ATAU
   (b) file Excel hasil Step 9 yang SUDAH DIEDIT analis lalu diunggah lewat form
   field `xlsx_file`. Bila `xlsx_file` ada, itu yang dipakai.

2) REKAP PER NOMOR REKAMAN (Bug 3): sheet baru "Rekap LM" — satu baris per
   Nomor Rekaman (kolom "ID Rekaman"/"ID Percakapan"), dengan kolom CATATAN_LM
   berisi hitung MKA / MKTA / UMK + catatan, contoh isi 1 sel:
       Matched Kontent Akurat: 4
       Matched Kontent Tidak Akurat: 0
       Unmatched Kontent: 1
       Catatan Matched Kontent:
       Catatan Unmatched Kontent: Menambahkan frasa '...' sebagai training phrase intent '...'

   Klasifikasi (per instruksi analis, sumber = sheet Analisis MKTA + Analisis Fallback):
   - MKA  (Matched Kontent Akurat)        = baris Analisis MKTA PUTUSAN = MENJAWAB
   - MKTA (Matched Kontent Tidak Akurat)  = baris Analisis MKTA PUTUSAN selain MENJAWAB
                                            (SALAH_INTENT / KURANG_LENGKAP / INTENT_BARU)
   - UMK  (Unmatched Kontent)             = baris Analisis Fallback (bot fallback)
   Catatan Matched  = catatan analis / "Alihkan ke intent 'X'" dari baris MKTA.
   Catatan Unmatched= "Menambahkan frasa '<pertanyaan>' sebagai training phrase
                       intent '<Intent Judgement LLM>'" dari baris Fallback TINDAK LANJUT.
   CATATAN: sheet "Analisis MKTA" hanya memuat baris < ambang Step 8, jadi MKA di
   sini = baris di-bawah-ambang yang diputus MENJAWAB (sesuai lingkup 2 sheet itu).

3) LM & Pembaruan (format lama) tetap ditulis + CSV (part=lm / part=pembaruan).

PEMASANGAN: `import step10_patch` SETELAH pipeline_routes diimpor (web_app.py).
dispatch() memakai late-binding global step10_build.
"""
import os
import re
import pipeline.routes as pr

# Kandidat header identitas rekaman (Nomor Rekaman). Urutan = prioritas.
ID_REKAMAN_HEADERS = ["ID Rekaman", "ID Percakapan", "id_rekaman", "ID trace", "ID Trace", "IDtrace"]


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


def _norm(s):
    return (s or "").strip().upper()


def _classify_put(put):
    """MENJAWAB -> MKA; PUTUSAN lain yang tak kosong -> MKTA; kosong -> None."""
    p = _norm(put).replace(" ", "_")
    if p == "":
        return None
    if p == "MENJAWAB":
        return "MKA"
    return "MKTA"


def _merge_pem(dst, src):
    for intent, items in src.items():
        dst.setdefault(intent, [])
        for phrase, tgl in items:
            if not any(p == phrase for p, _ in dst[intent]):
                dst[intent].append((phrase, tgl))


def _collect_mkta(wb):
    """Baris TINDAK LANJUT dari sheet Analisis MKTA (hasil Step 9) untuk sheet LM."""
    lm = []
    pem = {}
    if "Analisis MKTA" not in wb.sheetnames:
        return lm, pem
    am = pr.read_sheet(wb["Analisis MKTA"])
    H = am["headers"]
    c_id = pr._find_header(H, ID_REKAMAN_HEADERS)
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
    """Baris TINDAK LANJUT dari sheet Analisis Fallback (Step 6, opsional) untuk sheet LM."""
    lm = []
    pem = {}
    if "Analisis Fallback" not in wb.sheetnames:
        return lm, pem
    af = pr.read_sheet(wb["Analisis Fallback"])
    H = af["headers"]
    c_id = pr._find_header(H, ["ID Percakapan", "ID Rekaman", "id_rekaman", "ID trace", "ID Trace", "InsertId", "InserId"])
    c_user = pr._find_header(H, ["Pertanyaan User", "user phrase", "Pertanyaan"])
    c_intent = pr._find_header(H, ["Intent Judgement LLM", "Intent Seharusnya"])
    c_cat = pr._find_header(H, ["Catatan LLM", "Catatan"])
    c_isi = pr._find_header(H, ["Isi Intent", "bot response", "Jawaban Bot"])
    c_waktu = pr._find_header(H, ["Tanggal Rekaman", "waktu interaksi", "Waktu Interaksi"])
    for rn in sorted(af["rows"].keys()):
        if rn == 1:
            continue
        cells = af["rows"][rn]
        seharusnya = pr._sv(cells, c_intent).strip()
        if seharusnya == "":
            continue
        user = pr._sv(cells, c_user)
        tgl = _tgl(pr._sv(cells, c_waktu)) if c_waktu else ""
        lm.append(["Fallback", pr._sv(cells, c_id), user, pr._sv(cells, c_isi),
                   "", "TINDAK LANJUT", seharusnya, pr._sv(cells, c_cat), tgl])
        if user.strip() != "":
            pem.setdefault(seharusnya, [])
            if not any(p == user for p, _ in pem[seharusnya]):
                pem[seharusnya].append((user, tgl))
    return lm, pem


def _build_rekap(wb_mkta, wb_fb, penyusun):
    """Rekap MKA/MKTA/UMK + CATATAN_LM, satu baris per Nomor Rekaman."""
    rek = {}
    order = []

    def ensure(rid):
        rid = rid if rid else "(tanpa ID Rekaman)"
        if rid not in rek:
            rek[rid] = {"mka": 0, "mkta": 0, "umk": 0, "mnotes": [], "unotes": [], "tgl": ""}
            order.append(rid)
        return rek[rid]

    if wb_mkta is not None and "Analisis MKTA" in wb_mkta.sheetnames:
        am = pr.read_sheet(wb_mkta["Analisis MKTA"])
        H = am["headers"]
        c_id = pr._find_header(H, ID_REKAMAN_HEADERS)
        c_put = pr._find_header(H, ["PUTUSAN"])
        c_seharusnya = pr._find_header(H, ["Intent Seharusnya", "Intent Seharusnya (Manual)"])
        c_llm = pr._find_header(H, ["Intent Seharusnya (LLM)"])
        c_cat = pr._find_header(H, ["Catatan"])
        c_waktu = pr._find_header(H, ["waktu interaksi", "Waktu Interaksi"])
        for rn in sorted(am["rows"].keys()):
            if rn == 1:
                continue
            cells = am["rows"][rn]
            cls = _classify_put(pr._sv(cells, c_put))
            if cls is None:
                continue
            e = ensure(pr._sv(cells, c_id).strip())
            if not e["tgl"]:
                e["tgl"] = _tgl(pr._sv(cells, c_waktu))
            if cls == "MKA":
                e["mka"] += 1
            else:
                e["mkta"] += 1
                cat = pr._sv(cells, c_cat).strip()
                seh = pr._sv(cells, c_seharusnya).strip() or pr._sv(cells, c_llm).strip()
                note = cat if cat else (("Alihkan ke intent '" + seh + "'") if seh else "")
                if note and note not in e["mnotes"]:
                    e["mnotes"].append(note)

    if wb_fb is not None and "Analisis Fallback" in wb_fb.sheetnames:
        af = pr.read_sheet(wb_fb["Analisis Fallback"])
        H = af["headers"]
        c_id = pr._find_header(H, ["ID Percakapan", "ID Rekaman", "id_rekaman", "ID trace", "ID Trace"])
        c_user = pr._find_header(H, ["Pertanyaan User", "user phrase", "Pertanyaan"])
        c_intent = pr._find_header(H, ["Intent Judgement LLM", "Intent Seharusnya"])
        c_waktu = pr._find_header(H, ["Tanggal Rekaman", "waktu interaksi", "Waktu Interaksi"])
        for rn in sorted(af["rows"].keys()):
            if rn == 1:
                continue
            cells = af["rows"][rn]
            rid = pr._sv(cells, c_id).strip()
            user = pr._sv(cells, c_user).strip()
            intent = pr._sv(cells, c_intent).strip()
            if rid == "" and user == "" and intent == "":
                continue
            e = ensure(rid)
            if not e["tgl"]:
                e["tgl"] = _tgl(pr._sv(cells, c_waktu))
            e["umk"] += 1
            if intent and user:
                note = "Menambahkan frasa '" + user + "' sebagai training phrase intent '" + intent + "'"
                if note not in e["unotes"]:
                    e["unotes"].append(note)

    header = ["Nomor Rekaman", "Matched Kontent Akurat", "Matched Kontent Tidak Akurat",
              "Unmatched Kontent", "CATATAN_LM", "NAMA PENYUSUN", "TGL Penyusunan"]
    out = [header]
    for rid in order:
        e = rek[rid]
        catatan_lm = (
            "Matched Kontent Akurat: " + str(e["mka"]) + "\n" +
            "Matched Kontent Tidak Akurat: " + str(e["mkta"]) + "\n" +
            "Unmatched Kontent: " + str(e["umk"]) + "\n" +
            "Catatan Matched Kontent: " + " / ".join(e["mnotes"]) + "\n" +
            "Catatan Unmatched Kontent: " + " / ".join(e["unotes"])
        )
        out.append([rid, e["mka"], e["mkta"], e["umk"], catatan_lm, penyusun, e["tgl"]])
    return out


def step10_build(cfg, ctx):
    penyusun = (ctx.P("penyusun", "") or "").strip()
    up = ctx.file("xlsx_file")
    if up is not None:
        b9 = up[0]
        try:
            with open(os.path.join(pr.run_dir(cfg, ctx.run), "step10_source.xlsx"), "wb") as f:
                f.write(b9)
        except Exception:
            pass
        sumber_src = "Unggahan analis (Excel Step 9 hasil edit)"
    else:
        state = pr.load_state(cfg, ctx.run)
        s9 = state["steps"].get("9")
        if not s9 or not s9.get("file"):
            raise Exception("Hasil Step 9 belum ada. Jalankan Step 9 dulu, atau unggah file Excel hasil Step 9 yang sudah diedit.")
        p9 = os.path.join(pr.run_dir(cfg, ctx.run), s9["file"])
        if not os.path.isfile(p9):
            raise Exception("File hasil Step 9 hilang dari server.")
        with open(p9, "rb") as f:
            b9 = f.read()
        sumber_src = "Artefak Step 9"
    wb9 = pr._wb_from_bytes(b9)
    if "Analisis MKTA" not in wb9.sheetnames:
        raise Exception('Sheet "Analisis MKTA" tidak ada. Jalankan Step 9 dulu, atau unggah Excel Step 9 yang benar.')

    lm_data = []
    pem_map = {}
    lm_m, pem_m = _collect_mkta(wb9)
    lm_data.extend(lm_m)
    _merge_pem(pem_map, pem_m)

    # Sumber Fallback: dari wb9 (bila analis menggabung) atau artefak Step 6.
    wb_fb = wb9 if "Analisis Fallback" in wb9.sheetnames else None
    if wb_fb is None:
        state = pr.load_state(cfg, ctx.run)
        s6 = state["steps"].get("6")
        if s6 and s6.get("file"):
            p6 = os.path.join(pr.run_dir(cfg, ctx.run), s6["file"])
            if os.path.isfile(p6):
                try:
                    with open(p6, "rb") as f:
                        b6 = f.read()
                    wb_fb = pr._wb_from_bytes(b6)
                except Exception:
                    wb_fb = None
    if wb_fb is not None:
        lm_f, pem_f = _collect_fallback(wb_fb)
        lm_data.extend(lm_f)
        _merge_pem(pem_map, pem_f)

    lm_header = ["Sumber", "ID trace", "user phrase", "bot response", "intent name",
                 "PUTUSAN", "Intent Seharusnya", "Catatan", "TGL Penyusunan"]
    lm_rows = [lm_header] + lm_data

    pem_header = ["Intent Seharusnya", "Training Phrase Baru", "NAMA PENYUSUN", "TGL Penyusunan"]
    pem_rows = [pem_header]
    for intent in sorted(pem_map.keys()):
        for phrase, tgl in pem_map[intent]:
            pem_rows.append([intent, phrase, penyusun, tgl])

    rekap_rows = _build_rekap(wb9, wb_fb, penyusun)

    out_bytes = pr.xlsx_upsert_sheet(b9, "Rekap LM", rekap_rows)
    out_bytes = pr.xlsx_upsert_sheet(out_bytes, "LM", lm_rows)
    out_bytes = pr.xlsx_upsert_sheet(out_bytes, "Pembaruan", pem_rows)
    d = pr.run_dir(cfg, ctx.run)
    with open(os.path.join(d, "step10_lm.csv"), "wb") as f:
        f.write(pr._csv_bytes(lm_rows))
    with open(os.path.join(d, "step10_pembaruan.csv"), "wb") as f:
        f.write(pr._csv_bytes(pem_rows))
    rekap_csv = [rekap_rows[0]] + [[r[0], r[1], r[2], r[3], str(r[4]).replace("\n", " | "), r[5], r[6]] for r in rekap_rows[1:]]
    with open(os.path.join(d, "step10_rekap.csv"), "wb") as f:
        f.write(pr._csv_bytes(rekap_csv))
    summary = {"status": "Selesai", "penyusun": (penyusun or "(kosong)"),
               "sumber": sumber_src, "nomor_rekaman": len(rekap_rows) - 1,
               "baris_LM": len(lm_rows) - 1, "baris_Pembaruan": len(pem_rows) - 1,
               "catatan": "Excel (Rekap LM + LM + Pembaruan) + CSV siap diunduh. CATATAN_LM per Nomor Rekaman berisi hitung MKA/MKTA/UMK."}
    data = pr.save_artifact(cfg, ctx.run, 10, "xlsx", out_bytes, "laporan_LM_dan_pembaruan.xlsx", summary)
    return {"step": 10, "artifact": data, "lm_rows": len(lm_rows) - 1,
            "pembaruan_rows": len(pem_rows) - 1, "rekap_rows": len(rekap_rows) - 1}


# Terapkan monkey-patch (late-binding global di dispatch()).
pr.step10_build = step10_build
print("[step10_patch] step10_build diperbarui (upload Excel Step 9 manual + Rekap LM CATATAN_LM per Nomor Rekaman).", flush=True)
