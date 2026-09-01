# -*- coding: utf-8 -*-
"""step11_zipfield_patch.py — Perbaikan Step 11 (Pembaruan Intent).

DUA MASALAH YANG DIPERBAIKI:

1) HTTP 422 'zip_file Field required' (LAMA):
   backend /api/update-usersays MEWAJIBKAN field berkas bernama `zip_file`,
   sedangkan handler lama meneruskan ZIP dengan nama field `df_zip`. -> tetap
   BACA dari unggahan frontend (ctx.file("df_zip")) lalu TERUSKAN sebagai
   `zip_file`.

2) HTTP 400 {"detail":"phrases JSON tidak valid: phrases harus berupa array
   JSON"} (BARU): backend mewajibkan field `phrases` berupa ARRAY JSON berisi
   objek {"id": intent, "tp": training_phrase, "lang": kode_bahasa}. Namun
   s11_derive_phrases mengembalikan DICT {intent: [phrase,...]} tanpa bahasa,
   dan handler lama mengirim dict itu apa adanya (json.dumps) -> ditolak backend.
   -> KONVERSI dict menjadi array [{id,tp,lang}].
      * lang = bahasa run (Step 1 memaksa satu run = satu bahasa id/en); dibaca
        dari kolom 'lang' pada sheet upstream di workbook Step 10. Bila kode
        bahasa run tak ada di ZIP tapi ZIP hanya punya satu bahasa, pakai bahasa
        ZIP itu (fallback aman). Default 'id'.

Juga: 'report' dari backend berbentuk LIST [intent, lang, phrase, status, file,
keterangan] (bukan dict), sehingga sheet "Status Pembaruan" kini dibaca sesuai
bentuk list itu (sebelumnya r.get(...) selalu gagal -> sheet kosong).

Logika derivasi frasa & format Excel TIDAK diubah; hanya BENTUK payload ke
backend dan pembacaan report yang disesuaikan dengan kontrak backend.

PEMASANGAN: di-chain-import dari step9_patch (SETELAH pipeline_routes siap).
dispatch() memakai late-binding global sehingga otomatis memakai versi ini.
"""
import os
import io
import re
import json
import base64
import zipfile
import pipeline.routes as pr


def _run_lang(b10):
    """Tentukan bahasa run ('id'/'en') dari workbook Step 10. Satu run = satu
    bahasa (Step 1). Pindai sheet upstream yang punya kolom 'lang'; ambil nilai
    non-kosong pertama. Default 'id'."""
    try:
        wb = pr._wb_from_bytes(b10)
    except Exception:
        return "id"
    for nm in ("Analisis MKTA", "Interaksi", "Non Fallback", "Fallback", "System"):
        if nm not in wb.sheetnames:
            continue
        try:
            sh = pr.read_sheet(wb[nm])
        except Exception:
            continue
        c_lang = pr._find_header(sh["headers"], ["lang", "Lang", "bahasa", "Bahasa"])
        if not c_lang:
            continue
        for rn in sorted(sh["rows"].keys()):
            if rn == 1:
                continue
            v = pr._sv(sh["rows"][rn], c_lang).strip().lower()
            if v:
                return "en" if v.startswith("en") else "id"
    return "id"


def _zip_langs(zip_bytes):
    """Kumpulan kode bahasa usersays yang ADA di ZIP (mis. {'id','en'})."""
    langs = set()
    try:
        z = zipfile.ZipFile(io.BytesIO(zip_bytes))
        for nm in z.namelist():
            if nm.endswith("/"):
                continue
            base = os.path.basename(nm)
            m = re.match(r"^(.*)_usersays_([A-Za-z]+)\.json$", base)
            if m:
                langs.add(m.group(2).lower())
        z.close()
    except Exception:
        pass
    return langs


def _phrases_to_items(phrases, lang):
    """Ubah hasil s11_derive_phrases menjadi array [{id,tp,lang}] yang diminta
    backend. Menerima dict {intent:[phrase,...]} (bentuk saat ini) maupun list
    (bila kelak sudah berbentuk item)."""
    items = []
    if isinstance(phrases, dict):
        for intent, tps in phrases.items():
            intent = str(intent).strip()
            if intent == "":
                continue
            if isinstance(tps, (list, tuple, set)):
                seq = list(tps)
            else:
                seq = [tps]
            for tp in seq:
                tp = str(tp).strip()
                if tp == "":
                    continue
                items.append({"id": intent, "tp": tp, "lang": lang})
    elif isinstance(phrases, list):
        for it in phrases:
            if isinstance(it, dict):
                iid = str(it.get("id", it.get("intent", ""))).strip()
                tp = str(it.get("tp", it.get("phrase", ""))).strip()
                lg = str(it.get("lang", "") or lang).strip().lower() or lang
                if iid and tp:
                    items.append({"id": iid, "tp": tp, "lang": lg})
    return items


def step11_update(cfg, ctx):
    raw_base = (ctx.P("ngrok_url") or "").strip()
    endpoint = pr.api_endpoint(cfg, raw_base, "/api/update-usersays")
    if not cfg["force_local_api"] and raw_base != "":
        pr.save_ngrok(cfg, ctx.run, raw_base)
    df_zip = ctx.file("df_zip")
    if df_zip is None:
        raise Exception("Unggah ZIP export Dialogflow (df_zip) yang berisi folder intents.")
    b10, phrases = pr.s11_derive_phrases(cfg, ctx)

    # Bahasa run untuk setiap frasa (satu run = satu bahasa). Fallback ke bahasa
    # tunggal yang ada di ZIP bila kode bahasa run tidak cocok dengan ZIP.
    lang = _run_lang(b10)
    zlangs = _zip_langs(df_zip[0])
    if zlangs and lang not in zlangs and len(zlangs) == 1:
        lang = next(iter(zlangs))

    items = _phrases_to_items(phrases, lang)
    if not items:
        raise Exception("Tidak ada training phrase baru untuk dikirim ke backend.")

    # PENTING: backend butuh `phrases` = ARRAY JSON [{id,tp,lang}] dan file `zip_file`.
    fields = {"phrases": json.dumps(items, ensure_ascii=False)}
    files = {"zip_file": (df_zip[0], df_zip[1] or "dialogflow.zip", "application/zip")}
    res = pr.curl_post_json(cfg, endpoint, files, fields)
    if not isinstance(res, dict) or not res.get("ok", True):
        raise Exception("Server gagal memproses: " + json.dumps(res, ensure_ascii=False)[:400])
    zip_b64 = res.get("zip_b64", "")
    if zip_b64 == "":
        raise Exception("Server tidak mengembalikan ZIP hasil (zip_b64 kosong).")
    zip_bytes = base64.b64decode(zip_b64)
    with open(os.path.join(pr.run_dir(cfg, ctx.run), "step11_usersays.zip"), "wb") as f:
        f.write(zip_bytes)
    stats = res.get("stats", {})
    report = res.get("report", [])
    # report backend = list baris [intent, lang, phrase, status, file, keterangan].
    st_header = ["Intent", "Bahasa", "Phrase", "Status", "File", "Keterangan"]
    st_rows = [st_header]
    if isinstance(report, list):
        for r in report:
            if isinstance(r, (list, tuple)):
                row = list(r) + [""] * (len(st_header) - len(r))
                st_rows.append([row[0], row[1], row[2], row[3], row[4], row[5]])
            elif isinstance(r, dict):
                st_rows.append([r.get("intent", ""), r.get("lang", ""),
                                r.get("phrase", r.get("tp", "")),
                                r.get("status", ""), r.get("file", ""),
                                r.get("note", r.get("keterangan", ""))])
    state = pr.load_state(cfg, ctx.run)
    s10 = state["steps"].get("10")
    out_bytes = None
    if s10 and s10.get("file"):
        p = os.path.join(pr.run_dir(cfg, ctx.run), s10["file"])
        if os.path.isfile(p):
            with open(p, "rb") as f:
                out_bytes = pr.xlsx_upsert_sheet(f.read(), "Status Pembaruan", st_rows)
    if out_bytes is None:
        out_bytes = pr.xlsx_build([{"name": "Status Pembaruan", "rows": st_rows}])
    summary = {"status": "Selesai", "statistik": stats, "bahasa": lang,
               "frasa_dikirim": len(items),
               "catatan": 'ZIP usersays siap diunduh. Sheet "Status Pembaruan" dibuat.'}
    data = pr.save_artifact(cfg, ctx.run, 11, "xlsx", out_bytes, "status_pembaruan_intent.xlsx", summary)
    return {"step": 11, "artifact": data, "stats": stats}


pr.step11_update = step11_update
print("[step11_zipfield_patch] Step 11: zip_file + phrases array [{id,tp,lang}] + report list.", flush=True)
