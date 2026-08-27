# -*- coding: utf-8 -*-
"""step11_zipfield_patch.py — Perbaikan Step 11 (Pembaruan Intent) error HTTP 422
'zip_file Field required'.

MASALAH: backend /api/update-usersays MEWAJIBKAN field berkas bernama `zip_file`,
sedangkan step11_update lama meneruskan ZIP dengan nama field `df_zip` (nama yang
dipakai FRONTEND saat mengUNGGAH ke aplikasi ini). Akibatnya backend menolak
dengan 422 {"detail":[{"type":"missing","loc":["body","zip_file"],...}]} walau
file ZIP sudah diunggah pengguna.

PERBAIKAN: tetap BACA ZIP dari unggahan frontend seperti biasa
(ctx.file("df_zip")), lalu TERUSKAN ke backend memakai nama field yang benar:
`zip_file`. Sisa alur (derivasi phrases dari Step 10, perakitan sheet
"Status Pembaruan", penyimpanan ZIP hasil) TIDAK berubah.

PEMASANGAN: di-chain-import dari step9_patch (SETELAH pipeline_routes siap,
fungsi step sudah ada). dispatch() memakai late-binding global sehingga otomatis
memakai versi ini.
"""
import os
import json
import base64
import pipeline.routes as pr


def step11_update(cfg, ctx):
    raw_base = (ctx.P("ngrok_url") or "").strip()
    endpoint = pr.api_endpoint(cfg, raw_base, "/api/update-usersays")
    if not cfg["force_local_api"] and raw_base != "":
        pr.save_ngrok(cfg, ctx.run, raw_base)
    df_zip = ctx.file("df_zip")
    if df_zip is None:
        raise Exception("Unggah ZIP export Dialogflow (df_zip) yang berisi folder intents.")
    _, phrases = pr.s11_derive_phrases(cfg, ctx)
    fields = {"phrases": json.dumps(phrases, ensure_ascii=False)}
    # PENTING: backend mewajibkan nama field `zip_file` (bukan `df_zip`).
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
    st_header = ["Intent", "Status", "Phrase Ditambahkan", "Catatan"]
    st_rows = [st_header]
    if isinstance(report, list):
        for r in report:
            if isinstance(r, dict):
                st_rows.append([r.get("intent", ""), r.get("status", ""),
                                r.get("added", r.get("phrases_added", "")), r.get("note", "")])
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
    summary = {"status": "Selesai", "statistik": stats,
               "catatan": 'ZIP usersays siap diunduh. Sheet "Status Pembaruan" dibuat.'}
    data = pr.save_artifact(cfg, ctx.run, 11, "xlsx", out_bytes, "status_pembaruan_intent.xlsx", summary)
    return {"step": 11, "artifact": data, "stats": stats}


pr.step11_update = step11_update
print("[step11_zipfield_patch] Step 11: field ZIP ke backend diperbaiki df_zip -> zip_file.", flush=True)
