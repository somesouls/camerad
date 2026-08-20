# [fix_step10_report] applied
def _s10_date(v):
    """Normalisasi nilai tanggal -> 'YYYY-MM-DD' (best-effort), meniru s10_date PHP."""
    v = ("" if v is None else str(v)).strip()
    if v == "":
        return ""
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", v)
    if m:
        return m.group(1)
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d",
                "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            return _dt.datetime.strptime(v[:19], fmt).strftime("%Y-%m-%d")
        except Exception:
            pass
    try:
        return _dt.datetime.strptime(v[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
    except Exception:
        return v


def step10_build(cfg, ctx):
    state = load_state(cfg, ctx.run)
    s9 = state["steps"].get("9")
    if not s9 or not s9.get("file"):
        raise Exception("Hasil Step 9 belum ada. Jalankan Step 9 dulu.")
    p = os.path.join(run_dir(cfg, ctx.run), s9["file"])
    if not os.path.isfile(p):
        raise Exception("File hasil Step 9 hilang dari server.")
    with open(p, "rb") as f:
        b = f.read()
    wb = _wb_from_bytes(b)

    def _read(name):
        if name not in wb.sheetnames:
            return None
        return read_sheet(wb[name])

    nonfb = _read("Non Fallback")
    afb = _read("Analisis Fallback")
    qa = _read("QA Conf MKTA")
    amkta = _read("Analisis MKTA")

    ID = {}

    def _ensure(_id):
        if _id not in ID:
            ID[_id] = {"nonfb": 0, "umk": 0, "mkta": 0, "date": "", "fb": [], "mk": []}

    penyusun = (ctx.P("penyusun", "") or "").strip()
    today = _dt.datetime.now().strftime("%Y-%m-%d")

    # 1) Non Fallback -> total interaksi matched per ID + tanggal
    if nonfb:
        H = nonfb["headers"]
        c_id = _find_header(H, ["ID trace", "ID Rekaman", "ID Trace"])
        c_wk = _find_header(H, ["waktu interaksi", "Waktu Interaksi", "timestamp"])
        for rn in sorted(nonfb["rows"].keys()):
            if rn == 1:
                continue
            cells = nonfb["rows"][rn]
            _id = _sv(cells, c_id).strip()
            if _id == "":
                continue
            _ensure(_id)
            ID[_id]["nonfb"] += 1
            if ID[_id]["date"] == "" and c_wk:
                dd = _s10_date(_sv(cells, c_wk))
                if dd != "":
                    ID[_id]["date"] = dd

    # 2) Analisis Fallback -> UMK per ID + item TINDAK LANJUT (Intent Judgement LLM terisi)
    if afb:
        H = afb["headers"]
        c_id = _find_header(H, ["ID Percakapan", "ID Rekaman", "ID trace"])
        c_ins = _find_header(H, ["InsertId", "InserId"])
        c_q = _find_header(H, ["Pertanyaan User", "user phrase"])
        c_intent = _find_header(H, ["Intent Judgement LLM"])
        c_tgl = _find_header(H, ["Tanggal Rekaman", "Waktu Interaksi", "waktu interaksi"])
        for rn in sorted(afb["rows"].keys()):
            if rn == 1:
                continue
            cells = afb["rows"][rn]
            _id = _sv(cells, c_id).strip()
            if _id == "":
                continue
            _ensure(_id)
            ID[_id]["umk"] += 1
            if ID[_id]["date"] == "" and c_tgl:
                dd = _s10_date(_sv(cells, c_tgl))
                if dd != "":
                    ID[_id]["date"] = dd
            intent = _sv(cells, c_intent).strip()
            if intent != "":
                ID[_id]["fb"].append({
                    "pertanyaan": _sv(cells, c_q),
                    "intent": intent,
                    "insertid": _sv(cells, c_ins),
                    "date": _s10_date(_sv(cells, c_tgl)) if c_tgl else "",
                })

    # 3) QA Conf MKTA -> peta komposit (Pertanyaan+Bot) => {id, insertid, date}
    qa_map = {}
    if qa:
        H = qa["headers"]
        c_id = _find_header(H, ["ID Rekaman", "ID trace", "ID Trace"])
        c_ins = _find_header(H, ["InsertId", "InserId"])
        c_q = _find_header(H, ["Pertanyaan User", "user phrase"])
        c_b = _find_header(H, ["Bot Response", "bot response", "Jawaban Bot"])
        c_wk = _find_header(H, ["Waktu Interaksi", "waktu interaksi"])
        for rn in sorted(qa["rows"].keys()):
            if rn == 1:
                continue
            cells = qa["rows"][rn]
            key = _sv(cells, c_q) + "\x1f" + _sv(cells, c_b)
            if key not in qa_map:
                qa_map[key] = {
                    "id": _sv(cells, c_id).strip(),
                    "insertid": _sv(cells, c_ins),
                    "date": _s10_date(_sv(cells, c_wk)) if c_wk else "",
                }

    # 4) Analisis MKTA -> item MKTA TINDAK LANJUT (Intent Seharusnya terisi)
    if amkta:
        H = amkta["headers"]
        c_q = _find_header(H, ["Pertanyaan User", "user phrase"])
        c_b = _find_header(H, ["Bot Response", "bot response", "Jawaban Bot"])
        c_seh = _find_header(H, ["Intent Seharusnya", "Intent Seharusnya (LLM)"])
        for rn in sorted(amkta["rows"].keys()):
            if rn == 1:
                continue
            cells = amkta["rows"][rn]
            seh = _sv(cells, c_seh).strip()
            if seh == "" or seh.upper() in ("TIDAK ADA", "-", "N/A", "TIDAK TERBACA"):
                continue  # hanya TINDAK LANJUT
            q = _sv(cells, c_q)
            bb = _sv(cells, c_b)
            info = qa_map.get(q + "\x1f" + bb, {"id": "", "insertid": "", "date": ""})
            _id = info["id"]
            if _id == "":
                continue
            _ensure(_id)
            ID[_id]["mkta"] += 1
            if ID[_id]["date"] == "" and info["date"] != "":
                ID[_id]["date"] = info["date"]
            ID[_id]["mk"].append({
                "pertanyaan": q, "intent": seh,
                "insertid": info["insertid"], "date": info["date"],
            })

    # ---- Bangun baris LM (hanya ID dengan minimal 1 TINDAK LANJUT) ----
    lm_header = ["TGL_REKAMAN", "NOMOR_REKAMAN", "NM_AGENT", "HASIL_LM", "CATATAN_LM"]
    lm_aoa = [lm_header]
    pemb_header = ["INSERT_ID", "NAMA_MATERI", "TGL PENYUSUNAN", "NAMA PENYUSUN",
                   "RANGKUMAN", "STATUS MATERI", "KATEGORI"]
    pemb_aoa = [pemb_header]

    ids = sorted(ID.keys(), key=lambda x: (ID[x]["date"], x))
    for _id in ids:
        d = ID[_id]
        if len(d["fb"]) == 0 and len(d["mk"]) == 0:
            continue
        mkta = d["mkta"]
        mka = max(0, d["nonfb"] - mkta)
        umk = d["umk"]
        matched_notes = [
            "Bot tidak akurat dalam merespon query user. Menambahkan '"
            + it["pertanyaan"] + "' sebagai training phrase intent '" + it["intent"] + "'"
            for it in d["mk"]
        ]
        unmatched_notes = [
            "Menambahkan frasa '" + it["pertanyaan"]
            + "' sebagai training phrase intent " + it["intent"]
            for it in d["fb"]
        ]
        catatan = (
            "Matched Kontent Akurat: " + str(mka) + " \n"
            + "Matched Kontent Tidak Akurat: " + str(mkta) + " \n"
            + "Unmatched Kontent: " + str(umk) + " \n"
            + "Catatan Matched Kontent: "
            + ("\n".join(matched_notes) if matched_notes else "-") + " \n"
            + "Catatan Unmatched Kontent: "
            + ("\n".join(unmatched_notes) if unmatched_notes else "0")
        )
        lm_aoa.append([d["date"], _id, "CHATBOT", "TINDAK LANJUT", catatan])

        for it in d["fb"]:
            rang = ("Dasar Pembaruan (No Rekaman): " + _id
                    + " \nPerubahan: Menambahkan frasa '" + it["pertanyaan"]
                    + "' sebagai training phrase intent " + it["intent"])
            tglp = it["date"] if it["date"] != "" else (d["date"] if d["date"] != "" else today)
            pemb_aoa.append([it["insertid"], it["intent"], tglp, penyusun, rang,
                             "Pembaruan Materi LM", "Fallback"])
        for it in d["mk"]:
            rang = ("Dasar Pembaruan (No Rekaman): " + _id
                    + " \nPerubahan: Menambahkan frasa '" + it["pertanyaan"]
                    + "' sebagai training phrase intent " + it["intent"])
            tglp = it["date"] if it["date"] != "" else (d["date"] if d["date"] != "" else today)
            pemb_aoa.append([it["insertid"], it["intent"], tglp, penyusun, rang,
                             "Pembaruan Materi LM", "MKTA"])

    if len(lm_aoa) == 1:
        raise Exception("Tidak ada baris TINDAK LANJUT (Fallback/MKTA) untuk dilaporkan. Isi dulu Step 6 / Step 9.")

    # ---- Excel utama: tambah sheet LM & Pembaruan ----
    out_bytes = xlsx_upsert_sheet(b, "LM", lm_aoa)
    out_bytes = xlsx_upsert_sheet(out_bytes, "Pembaruan", pemb_aoa)

    d_dir = run_dir(cfg, ctx.run)
    # CSV LM: semua kolom
    with open(os.path.join(d_dir, "step10_lm.csv"), "wb") as f:
        f.write(_csv_bytes(lm_aoa))
    # CSV Pembaruan: hanya NAMA_MATERI..STATUS MATERI (buang INSERT_ID idx0 & KATEGORI idx6)
    pemb_csv = [row[1:6] for row in pemb_aoa]
    with open(os.path.join(d_dir, "step10_pembaruan.csv"), "wb") as f:
        f.write(_csv_bytes(pemb_csv))

    summary = {"status": "Selesai", "baris_LM": len(lm_aoa) - 1,
               "baris_Pembaruan": len(pemb_aoa) - 1,
               "catatan": "Excel + CSV LM + CSV Pembaruan siap diunduh."}
    data = save_artifact(cfg, ctx.run, 10, "xlsx", out_bytes, "Laporan_Utama.xlsx", summary)
    return {"step": 10, "artifact": data,
            "lm_rows": len(lm_aoa) - 1, "pembaruan_rows": len(pemb_aoa) - 1}
