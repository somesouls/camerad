# -*- coding: utf-8 -*-
"""step10_patch.py — Laporan Step 10 (LM + Rekap LM + Rekap Harian + Pembaruan).

RINGKAS PERUBAHAN:
1) LM = 1 BARIS PER NOMOR REKAMAN (ID Trace): Fallback ditindaklanjuti + SELURUH
   baris MKTA (tindak lanjut + kosong). HASIL_LM dari kolom "Intent Seharusnya"
   (manual Step 9): terisi -> TINDAK LANJUT; kosong -> TANPA CATATAN.
   Kolom: TGL_REKAMAN | NOMOR_REKAMAN | NM_AGENT | HASIL_LM | CATATAN_LM.
2) Sheet "Rekap Harian": 1 baris per Tanggal Rekaman. Kolom:
     No | Tanggal Rekaman | Hari | PIC | Target | Jatuh Tempo | Tgl Upload |
     Status Upload SIKLIP | Tepat Waktu | %MKTA | %Match Harian |
     Update Intent MKTA | Update Intent Fallback | Target Update |
     % Capaian Update | Intent Baru IND | Intent Baru EN | Update Bilingual |
     Materi Voice Bot
   - %MKTA & %Match Harian ditulis sebagai RUMUS berisi angka nyata, mis.
     '=3/(4383-146)' dan '=(4383-146)/4383' (bukan hasil persen jadi) supaya
     bisa diaudit. Di Excel jadi formula; di CSV jadi teks rumus.
   - Update Intent MKTA = jumlah MKTA tindak lanjut (Intent Seharusnya terisi).
   - Update Intent Fallback = jumlah Fallback tindak lanjut (Intent Judgement LLM).
   - Total interaksi & fallback per tanggal dari sheet "Interaksi" & "Fallback"
     (artefak Step 2); seluruh baris dihitung ("gapapa duplikat").
   - Kolom operasional lain dikosongkan untuk diisi manual analis.
3) Sheet/CSV "Pembaruan" (FORMAT BARU, laporan materi LM):
     NAMA_MATERI | TGL PENYUSUNAN | NAMA PENYUSUN | RANGKUMAN | STATUS MATERI
   - NAMA_MATERI = nama intent tujuan.
   - TGL PENYUSUNAN = tanggal interaksi/percakapan (YYYY-MM-DD).
   - NAMA PENYUSUN = dari form 'penyusun'.
   - RANGKUMAN = "Dasar Pembaruan (No Rekaman): <ID Trace> Perubahan: Menambahkan
     frasa '<user phrase>' sebagai training phrase intent <intent>".
   - STATUS MATERI = "Pembaruan Materi LM" (konstan).
   Sheet bersih "Data Pembaruan" (Intent Seharusnya | Training Phrase Baru | ...)
   tetap dibuat untuk Step 11; s11_derive_phrases di-monkey-patch membacanya.

SUMBER GANDA: memakai (a) artefak Step 9 di server, ATAU (b) Excel Step 9 hasil
edit yang diunggah lewat form 'xlsx_file'. Sheet Interaksi/Fallback (Step 2) &
Analisis Fallback (Step 6) dibaca dari artefak masing-masing (via state).

PEMASANGAN: import step10_patch SETELAH pipeline_routes diimpor (web_app.py).
"""
import os
import re
import datetime as _dt
import pipeline.routes as pr
import pipeline.steps as _ps

# Kandidat header identitas rekaman (Nomor Rekaman). Urutan = prioritas.
ID_REKAMAN_HEADERS = ["ID Rekaman", "ID Percakapan", "id_rekaman", "ID trace", "ID Trace", "IDtrace"]

# Nama agent default untuk kolom NM_AGENT (bisa dioverride via form `nm_agent`).
DEFAULT_NM_AGENT = "CHATBOT"

# Bulan & hari (Bahasa Indonesia) untuk kolom Tanggal Rekaman & Hari.
_ID_BULAN = ["", "Januari", "Februari", "Maret", "April", "Mei", "Juni",
             "Juli", "Agustus", "September", "Oktober", "November", "Desember"]
_ID_HARI = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]


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


def _iso_date(waktu):
    """Kembalikan (y, m, d) dari string waktu, atau None bila tak terbaca."""
    s = (waktu or "").strip()
    if s == "":
        return None
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", s)
    if m:
        return (int(m.group(3)), int(m.group(2)), int(m.group(1)))
    return None


def _tgl_id(iso):
    """(y,m,d) -> 'D Bulan YYYY' (Bahasa Indonesia). '' bila tak valid."""
    if not iso:
        return ""
    y, m, d = iso
    if not (1 <= m <= 12):
        return "%04d-%02d-%02d" % (y, m, d)
    return "%d %s %d" % (d, _ID_BULAN[m], y)


def _hari_id(iso):
    """(y,m,d) -> nama hari Bahasa Indonesia. '' bila tak valid."""
    if not iso:
        return ""
    try:
        return _ID_HARI[_dt.date(iso[0], iso[1], iso[2]).weekday()]
    except Exception:
        return ""


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


def _pem_mkta(wb):
    """Rekaman training phrase baru dari baris MKTA yang ditindaklanjuti
    (kolom "Intent Seharusnya" manual terisi). Return list of
    {intent, phrase, rid, tgl}."""
    out = []
    if wb is None or "Analisis MKTA" not in wb.sheetnames:
        return out
    am = pr.read_sheet(wb["Analisis MKTA"])
    H = am["headers"]
    c_id = pr._find_header(H, ID_REKAMAN_HEADERS)
    c_user = pr._find_header(H, ["user phrase", "Pertanyaan User"])
    c_seharusnya = pr._find_header(H, ["Intent Seharusnya", "Intent Seharusnya (Manual)"])
    c_waktu = pr._find_header(H, ["waktu interaksi", "Waktu Interaksi"])
    for rn in sorted(am["rows"].keys()):
        if rn == 1:
            continue
        cells = am["rows"][rn]
        intent = pr._sv(cells, c_seharusnya).strip()
        user = pr._sv(cells, c_user).strip()
        if intent == "" or user == "":
            continue
        out.append({"intent": intent, "phrase": user,
                    "rid": pr._sv(cells, c_id).strip(),
                    "tgl": _tgl(pr._sv(cells, c_waktu))})
    return out


def _pem_fallback(wb):
    """Rekaman training phrase baru dari baris Fallback yang ditindaklanjuti
    (kolom "Intent Judgement LLM" terisi). Return list of
    {intent, phrase, rid, tgl}."""
    out = []
    if wb is None or "Analisis Fallback" not in wb.sheetnames:
        return out
    af = pr.read_sheet(wb["Analisis Fallback"])
    H = af["headers"]
    c_id = pr._find_header(H, ["ID Percakapan", "ID Rekaman", "id_rekaman", "ID trace", "ID Trace"])
    c_user = pr._find_header(H, ["Pertanyaan User", "user phrase", "Pertanyaan"])
    c_intent = pr._find_header(H, ["Intent Judgement LLM", "Intent Seharusnya"])
    c_waktu = pr._find_header(H, ["Tanggal Rekaman", "waktu interaksi", "Waktu Interaksi"])
    for rn in sorted(af["rows"].keys()):
        if rn == 1:
            continue
        cells = af["rows"][rn]
        intent = pr._sv(cells, c_intent).strip()
        user = pr._sv(cells, c_user).strip()
        if intent == "" or user == "":
            continue
        out.append({"intent": intent, "phrase": user,
                    "rid": pr._sv(cells, c_id).strip(),
                    "tgl": (_tgl(pr._sv(cells, c_waktu)) if c_waktu else "")})
    return out


def _aggregate(wb_mkta, wb_fb):
    """Agregasi per Nomor Rekaman (ID Trace) untuk sheet LM & Rekap LM.

    Return (order, rek) di mana rek[rid] = {
        mka, mkta, umk, mnotes[], unotes[], tgl, follow(bool)
    }.
    """
    rek = {}
    order = []

    def ensure(rid):
        rid = rid if rid else "(tanpa ID Rekaman)"
        if rid not in rek:
            rek[rid] = {"mka": 0, "mkta": 0, "umk": 0, "mnotes": [], "unotes": [],
                        "tgl": "", "follow": False}
            order.append(rid)
        return rek[rid]

    # --- MKTA (Step 9): SEMUA baris (tindak lanjut + kosong) ---
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
            manual_seh = pr._sv(cells, c_seharusnya).strip()
            if cls is None and manual_seh == "":
                continue
            e = ensure(pr._sv(cells, c_id).strip())
            if not e["tgl"]:
                e["tgl"] = _tgl(pr._sv(cells, c_waktu))
            if manual_seh != "":
                e["follow"] = True
            if cls == "MKA":
                e["mka"] += 1
            elif cls == "MKTA":
                e["mkta"] += 1
                cat = pr._sv(cells, c_cat).strip()
                seh = manual_seh or pr._sv(cells, c_llm).strip()
                note = cat if cat else (("Alihkan ke intent '" + seh + "'") if seh else "")
                if note and note not in e["mnotes"]:
                    e["mnotes"].append(note)

    # --- Fallback (Step 6): Unmatched Kontent ---
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

    return order, rek


def _catatan_lm(e):
    """Rakit isi sel CATATAN_LM (hitung MKA/MKTA/UMK + catatan matched/unmatched)."""
    return (
        "Matched Kontent Akurat: " + str(e["mka"]) + "\n" +
        "Matched Kontent Tidak Akurat: " + str(e["mkta"]) + "\n" +
        "Unmatched Kontent: " + str(e["umk"]) + "\n" +
        "Catatan Matched Kontent: " + " / ".join(e["mnotes"]) + "\n" +
        "Catatan Unmatched Kontent: " + " / ".join(e["unotes"])
    )


def _hasil_lm(e):
    return "TINDAK LANJUT" if e["follow"] else "TANPA CATATAN"


# =============================================================
# REKAP HARIAN (per Tanggal Rekaman)
# =============================================================
REKAP_HARIAN_HEADER = [
    "No", "Tanggal Rekaman", "Hari", "PIC", "Target", "Jatuh Tempo",
    "Tgl Upload", "Status Upload SIKLIP", "Tepat Waktu",
    "%MKTA", "%Match Harian", "Update Intent MKTA", "Update Intent Fallback",
    "Target Update", "% Capaian Update", "Intent Baru IND", "Intent Baru EN",
    "Update Bilingual", "Materi Voice Bot",
]

_TGL_HEADERS = ["waktu interaksi", "Waktu Interaksi", "Tanggal Rekaman", "waktu"]


def _count_by_date(wb, sheet_name):
    """Hitung jumlah baris data per tanggal (tuple y,m,d) pada sebuah sheet.
    Baris tanpa tanggal terbaca dikelompokkan pada key None."""
    out = {}
    if wb is None or sheet_name not in wb.sheetnames:
        return out
    sh = pr.read_sheet(wb[sheet_name])
    c_wkt = pr._find_header(sh["headers"], _TGL_HEADERS)
    for rn in sorted(sh["rows"].keys()):
        if rn == 1:
            continue
        iso = _iso_date(pr._sv(sh["rows"][rn], c_wkt)) if c_wkt else None
        out[iso] = out.get(iso, 0) + 1
    return out


def _mkta_follow_by_date(wb):
    """Jumlah MKTA tindak lanjut per tanggal (Intent Seharusnya manual terisi)."""
    out = {}
    if wb is None or "Analisis MKTA" not in wb.sheetnames:
        return out
    am = pr.read_sheet(wb["Analisis MKTA"])
    H = am["headers"]
    c_seh = pr._find_header(H, ["Intent Seharusnya", "Intent Seharusnya (Manual)"])
    c_wkt = pr._find_header(H, ["waktu interaksi", "Waktu Interaksi"])
    for rn in sorted(am["rows"].keys()):
        if rn == 1:
            continue
        cells = am["rows"][rn]
        if pr._sv(cells, c_seh).strip() == "":
            continue
        iso = _iso_date(pr._sv(cells, c_wkt)) if c_wkt else None
        out[iso] = out.get(iso, 0) + 1
    return out


def _fallback_follow_by_date(wb):
    """Jumlah Fallback tindak lanjut per tanggal (Intent Judgement LLM terisi)."""
    out = {}
    if wb is None or "Analisis Fallback" not in wb.sheetnames:
        return out
    af = pr.read_sheet(wb["Analisis Fallback"])
    H = af["headers"]
    c_intent = pr._find_header(H, ["Intent Judgement LLM", "Intent Seharusnya"])
    c_wkt = pr._find_header(H, ["Tanggal Rekaman", "waktu interaksi", "Waktu Interaksi"])
    for rn in sorted(af["rows"].keys()):
        if rn == 1:
            continue
        cells = af["rows"][rn]
        if pr._sv(cells, c_intent).strip() == "":
            continue
        iso = _iso_date(pr._sv(cells, c_wkt)) if c_wkt else None
        out[iso] = out.get(iso, 0) + 1
    return out


def _build_rekap_harian(wb2, wb9, wb_fb):
    """Bangun baris sheet 'Rekap Harian' (1 baris per Tanggal Rekaman).
    %MKTA & %Match Harian ditulis sebagai rumus berisi angka nyata."""
    interaksi = _count_by_date(wb2, "Interaksi")
    fallback_total = _count_by_date(wb2, "Fallback")
    mkta_follow = _mkta_follow_by_date(wb9)
    fb_source = wb_fb if wb_fb is not None else wb9
    fb_follow = _fallback_follow_by_date(fb_source)

    dates = set()
    for mp in (interaksi, fallback_total, mkta_follow, fb_follow):
        dates.update(mp.keys())
    dates.discard(None)
    ordered = sorted(dates)

    rows = [REKAP_HARIAN_HEADER]
    for i, iso in enumerate(ordered, start=1):
        tot_int = interaksi.get(iso, 0)
        tot_fb = fallback_total.get(iso, 0)
        mk = mkta_follow.get(iso, 0)
        fb = fb_follow.get(iso, 0)
        match = tot_int - tot_fb
        pmkta = ("=%d/(%d-%d)" % (mk, tot_int, tot_fb)) if match != 0 else "0"
        pmatch = ("=(%d-%d)/%d" % (tot_int, tot_fb, tot_int)) if tot_int != 0 else "0"
        rows.append([
            i, _tgl_id(iso), _hari_id(iso), "", "", "",
            "", "", "",
            pmkta, pmatch, mk, fb,
            "", "", "", "",
            "", "",
        ])
    return rows


def _load_step_wb(cfg, ctx, step_no):
    """Muat artefak sebuah step sebagai workbook openpyxl (None bila tak ada)."""
    try:
        state = pr.load_state(cfg, ctx.run)
        s = state["steps"].get(str(step_no))
        if not s or not s.get("file"):
            return None
        p = os.path.join(pr.run_dir(cfg, ctx.run), s["file"])
        if not os.path.isfile(p):
            return None
        with open(p, "rb") as f:
            return pr._wb_from_bytes(f.read())
    except Exception:
        return None


def _rangkuman(rid, phrase, intent):
    return ("Dasar Pembaruan (No Rekaman): " + rid +
            " Perubahan: Menambahkan frasa '" + phrase +
            "' sebagai training phrase intent " + intent)


def step10_build(cfg, ctx):
    penyusun = (ctx.P("penyusun", "") or "").strip()
    nm_agent = (ctx.P("nm_agent", "") or "").strip() or DEFAULT_NM_AGENT
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

    # Sumber Fallback (Step 6): dari wb9 (bila analis menggabung) atau artefak Step 6.
    wb_fb = wb9 if "Analisis Fallback" in wb9.sheetnames else _load_step_wb(cfg, ctx, 6)

    # Sumber Step 2 (sheet "Interaksi" & "Fallback") untuk Rekap Harian.
    wb2 = _load_step_wb(cfg, ctx, 2)
    if wb2 is None and ("Interaksi" in wb9.sheetnames or "Fallback" in wb9.sheetnames):
        wb2 = wb9

    order, rek = _aggregate(wb9, wb_fb)

    # --- Sheet "LM" (1 baris per Nomor Rekaman) ---
    lm_header = ["TGL_REKAMAN", "NOMOR_REKAMAN", "NM_AGENT", "HASIL_LM", "CATATAN_LM"]
    lm_rows = [lm_header]
    for rid in order:
        e = rek[rid]
        lm_rows.append([e["tgl"], rid, nm_agent, _hasil_lm(e), _catatan_lm(e)])

    # --- Sheet "Rekap LM" (rincian angka + HASIL_LM) ---
    rekap_header = ["Nomor Rekaman", "TGL Rekaman", "NM Agent", "HASIL_LM",
                    "Matched Kontent Akurat", "Matched Kontent Tidak Akurat",
                    "Unmatched Kontent", "CATATAN_LM", "NAMA PENYUSUN", "TGL Penyusunan"]
    rekap_rows = [rekap_header]
    for rid in order:
        e = rek[rid]
        rekap_rows.append([rid, e["tgl"], nm_agent, _hasil_lm(e), e["mka"], e["mkta"],
                           e["umk"], _catatan_lm(e), penyusun, e["tgl"]])

    # --- Sheet "Rekap Harian" (per Tanggal Rekaman) ---
    rekap_harian_rows = _build_rekap_harian(wb2, wb9, wb_fb)

    # --- Rekaman training phrase baru (MKTA follow + Fallback follow) ---
    pem_records = _pem_mkta(wb9)
    if wb_fb is not None:
        pem_records = pem_records + _pem_fallback(wb_fb)

    # Sheet "Pembaruan" (FORMAT BARU: laporan materi LM), 1 baris per pembaruan.
    plm_header = ["NAMA_MATERI", "TGL PENYUSUNAN", "NAMA PENYUSUN", "RANGKUMAN", "STATUS MATERI"]
    plm_rows = [plm_header]
    seen_lm = set()
    for r in pem_records:
        key = (r["intent"], r["phrase"], r["rid"])
        if key in seen_lm:
            continue
        seen_lm.add(key)
        plm_rows.append([r["intent"], r["tgl"], penyusun,
                         _rangkuman(r["rid"], r["phrase"], r["intent"]), "Pembaruan Materi LM"])

    # Sheet "Data Pembaruan" (bersih, dipakai Step 11), dedup per (intent, phrase).
    data_header = ["Intent Seharusnya", "Training Phrase Baru", "NAMA PENYUSUN", "TGL Penyusunan"]
    data_rows = [data_header]
    seen_ip = set()
    for r in pem_records:
        key = (r["intent"], r["phrase"])
        if key in seen_ip:
            continue
        seen_ip.add(key)
        data_rows.append([r["intent"], r["phrase"], penyusun, r["tgl"]])

    out_bytes = pr.xlsx_upsert_sheet(b9, "LM", lm_rows)
    out_bytes = pr.xlsx_upsert_sheet(out_bytes, "Rekap LM", rekap_rows)
    out_bytes = pr.xlsx_upsert_sheet(out_bytes, "Rekap Harian", rekap_harian_rows)
    out_bytes = pr.xlsx_upsert_sheet(out_bytes, "Pembaruan", plm_rows)
    out_bytes = pr.xlsx_upsert_sheet(out_bytes, "Data Pembaruan", data_rows)
    d = pr.run_dir(cfg, ctx.run)
    with open(os.path.join(d, "step10_lm.csv"), "wb") as f:
        f.write(pr._csv_bytes(lm_rows))
    with open(os.path.join(d, "step10_pembaruan.csv"), "wb") as f:
        f.write(pr._csv_bytes(plm_rows))
    with open(os.path.join(d, "step10_rekap_harian.csv"), "wb") as f:
        f.write(pr._csv_bytes(rekap_harian_rows))
    rekap_csv = [rekap_rows[0]] + [
        [r[0], r[1], r[2], r[3], r[4], r[5], r[6], str(r[7]).replace("\n", " | "), r[8], r[9]]
        for r in rekap_rows[1:]
    ]
    with open(os.path.join(d, "step10_rekap.csv"), "wb") as f:
        f.write(pr._csv_bytes(rekap_csv))
    summary = {"status": "Selesai", "penyusun": (penyusun or "(kosong)"),
               "nm_agent": nm_agent, "sumber": sumber_src,
               "nomor_rekaman": len(lm_rows) - 1,
               "baris_LM": len(lm_rows) - 1, "baris_Rekap_Harian": len(rekap_harian_rows) - 1,
               "baris_Pembaruan": len(plm_rows) - 1,
               "catatan": "LM per Nomor Rekaman; Rekap Harian per Tanggal Rekaman (%MKTA/%Match "
                          "sebagai rumus berisi angka); Pembaruan format NAMA_MATERI/TGL/PENYUSUN/"
                          "RANGKUMAN/STATUS. Sheet 'Data Pembaruan' dipakai Step 11."}
    data = pr.save_artifact(cfg, ctx.run, 10, "xlsx", out_bytes, "laporan_LM_dan_pembaruan.xlsx", summary)
    return {"step": 10, "artifact": data, "lm_rows": len(lm_rows) - 1,
            "pembaruan_rows": len(plm_rows) - 1, "rekap_rows": len(rekap_rows) - 1,
            "rekap_harian_rows": len(rekap_harian_rows) - 1}


# =============================================================
# STEP 11 (patch) — derivasi training phrase dari sheet Pembaruan format baru
# =============================================================
def _s11_derive_phrases(cfg, ctx):
    """Ambil {intent: [phrase]} dari hasil Step 10. Utamakan sheet bersih
    "Data Pembaruan"; fallback ke "Pembaruan" (format lama kolom Training Phrase
    Baru, atau format baru dengan mengurai kolom RANGKUMAN)."""
    state = pr.load_state(cfg, ctx.run)
    s10 = state["steps"].get("10")
    if not s10 or not s10.get("file"):
        raise Exception("Hasil Step 10 belum ada. Jalankan Step 10 dulu.")
    p = os.path.join(pr.run_dir(cfg, ctx.run), s10[