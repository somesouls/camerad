# -*- coding: utf-8 -*-
"""store_rows_patch.py — Simpan hasil Step 4-8 sebagai BARIS (row-based) di
pipeline_store.db, bukan blob Excel.

Membungkus handler step4/5/6/7/8 di pipeline.routes: setelah handler ASLI
menyimpan artefak (save_artifact -> blob), blob diurai menjadi baris (step_row)
via rowstore lalu blob di-NULL-kan (record_step). Unduhan & input step
berikutnya dirakit ON-DEMAND dari baris (helpers.artifact_bytes / _materialize).
Idempoten & fail-open: bila konversi gagal, blob lama tetap dipakai.

Step 8 (chunked) HANYA dikonversi saat res["done"] True — chunk antara tetap
blob agar step8_base_bytes membaca cache disk pristine utk akumulasi backend.
Step 9 ditangani terpisah (step9_patch: step_row + step_edit kunci bisnis).

PEMASANGAN: di-chain-import dari step9_patch (SETELAH pipeline_routes dimuat,
fungsi step sudah ada). Fungsi *_load (sinyal/id_trace/acuan) TIDAK disentuh.
"""
import pipeline.routes as pr
from pipeline import helpers as H
from pipeline import store as pstore
from pipeline import rowstore


def _convert(cfg, run, n):
    """Urai blob Excel step n (baru disimpan handler asli) -> baris; NULL-kan blob.
    Fail-open & idempoten (blob sudah NULL / bukan xlsx -> lewati)."""
    n = int(n)
    conn = H._store()
    try:
        ds = H._resolve_dataset(conn, run, create=False)
        if not ds:
            return
        did = ds["id"]
        meta = pstore.get_artifact_meta(conn, did, n)
        if not meta or (meta.get("ext") or "").lower() != "xlsx":
            return
        b = pstore.get_artifact_bytes(conn, did, n)
        if b is None:
            return
        name = meta.get("name") or ("step%d.xlsx" % n)
        summary = meta.get("summary")
    finally:
        conn.close()
    rowstore.save_step_from_xlsx(cfg, run, n, b, "xlsx", name, summary)


def _wrap(name, n):
    orig = getattr(pr, name, None)
    if not callable(orig):
        print("[store_rows_patch] %s tidak ada; dilewati." % name, flush=True)
        return

    def wrapped(cfg, ctx):
        res = orig(cfg, ctx)
        try:
            done = True
            if isinstance(res, dict) and ("done" in res):
                done = bool(res.get("done"))
            if done:
                _convert(cfg, ctx.run, n)
        except Exception as e:
            print("[store_rows_patch] konversi step%d gagal (fail-open): %r" % (n, e), flush=True)
        return res

    wrapped.__name__ = getattr(orig, "__name__", name)
    setattr(pr, name, wrapped)


_wrap("step4_analyze", 4)
_wrap("step5_qwen_judge", 5)
_wrap("step6_save", 6)
_wrap("step7_mkta", 7)
_wrap("step8_run", 8)
print("[store_rows_patch] step4/5/6/7/8 -> penyimpanan baris (row-based) aktif.", flush=True)

# Rakit Excel secara LAZY (hanya saat unduh / dipakai step berikutnya), bukan tiap
# polling status atau tiap simpan. Menghilangkan beban perakitan Excel berulang
# TANPA mengubah data/logika/format/rumus. Di-chain di sini karena helpers &
# rowstore sudah pasti siap saat store_rows_patch dimuat.
import pipeline.lazy_excel_patch  # noqa: E402,F401
