# -*- coding: utf-8 -*-
"""lazy_excel_patch.py — Rakit Excel HANYA saat dibutuhkan (unduh / input step
berikutnya), bukan tiap polling status atau tiap simpan.

LATAR: Data Step 4–9 SUDAH disimpan berbasis BARIS di pipeline_store.db (bukan
blob Excel). TAPI Excel masih dirakit ulang secara EAGER di 3 titik sehingga
berat:
  1) helpers._materialize dipanggil oleh load_state SETIAP polling status, dan
     untuk tiap step berbasis baris ia MERAKIT ULANG Excel lalu menulis cache
     disk SETIAP KALI ("selalu tulis ulang"). Ini sumber beban utama: tiap poll
     merakit ulang Excel semua step (4,5,6,7,8,9).
  2) rowstore.save_step_rows — merakit + tulis cache disk tiap step dijalankan.
  3) rowstore.save_step_edits — merakit + tulis cache disk tiap editan disimpan.

PATCH (hanya mengubah KAPAN Excel dirakit; TIDAK mengubah data/logika/format/
rumus sama sekali):
  - _materialize: untuk step berbasis baris, rakit + tulis cache disk HANYA bila
    file BELUM ADA (mis. setelah restart / reset / cache dihapus). Bila sudah
    ada -> lewati (tidak merakit ulang tiap poll).
  - save_step_rows & save_step_edits: JANGAN merakit Excel; cukup simpan ke DB
    (sumber kebenaran) lalu HAPUS cache disk agar dirakit ulang secara lazy saat
    benar-benar dibutuhkan.

KESEGARAN & KEAMANAN:
  - Setiap mutasi (save_step_rows/save_step_edits) menghapus cache disk, jadi
    cache tak pernah basi.
  - Semua pembaca cache disk (step4/6/7/8 auto, step10, dst.) SELALU memanggil
    load_state lebih dulu -> _materialize merakit ulang bila file hilang.
  - Unduhan & transfer antar-step lewat helpers.artifact_bytes tetap merakit
    on-demand dari DB.
  - Excel dirakit oleh assemble_step_xlsx / xlsx_build yang SAMA PERSIS -> byte
    hasil identik; hanya WAKTU perakitan yang berubah.

YANG TIDAK DISENTUH: fungsi perakit (assemble_step_xlsx, xlsx_build), penyimpanan
artefak blob (save_artifact) untuk step berbasis blob (1,2,3,10,11,12–16), dan
SELURUH logika/format/rumus Step 10 (mis. rumus %MKTA / %Match Harian) yang
disimpan sebagai blob Excel apa adanya.

PEMASANGAN: di-chain-import dari store_rows_patch (yang sendiri di-chain dari
step9_patch), SETELAH helpers & rowstore siap. Override memakai late-binding
global sehingga load_state / save_step_from_xlsx / step9_patch otomatis memakai
versi lazy ini saat runtime.
"""
import os
import datetime as _dt

from pipeline import helpers as H
from pipeline import rowstore
from pipeline import store as pstore


def _delete_disk_cache(cfg, run, n, ext):
    """Hapus cache disk stepN.<ext> (bila ada) supaya dirakit ulang lazy."""
    try:
        p = os.path.join(H.run_dir(cfg, run), "step%s.%s" % (n, ext))
        if os.path.isfile(p):
            os.remove(p)
    except Exception:
        pass


def _materialize(cfg, run, dataset, conn):
    """Versi LAZY dari helpers._materialize.

    - Step berbasis BLOB: tulis cache disk bila belum ada / beda ukuran (sama
      seperti asli; murah, hanya menyalin bytes yang sudah ada di DB).
    - Step berbasis BARIS (blob NULL): rakit + tulis cache disk HANYA bila file
      belum ada. TIDAK merakit ulang tiap load_state (inilah perbaikan beban).
      Kesegaran dijaga save_step_rows/save_step_edits yang menghapus cache saat
      data berubah, jadi file yang ada pasti masih sesuai DB.
    """
    if not dataset:
        return
    d = H.run_dir(cfg, run)
    for a in pstore.list_artifacts(conn, dataset["id"]):
        step = a["step"]
        fname = "step%s.%s" % (step, a.get("ext") or "bin")
        p = os.path.join(d, fname)
        b = pstore.get_artifact_bytes(conn, dataset["id"], step)
        if b is None:
            # Step berbasis baris: rakit lazy, HANYA bila cache disk belum ada.
            if os.path.isfile(p):
                continue
            if not pstore.has_rows(conn, dataset["id"], step):
                continue
            try:
                b = rowstore.assemble_step_xlsx(conn, dataset["id"], step)
            except Exception:
                b = None
            if b is None:
                continue
            try:
                with open(p, "wb") as f:
                    f.write(b)
            except Exception:
                pass
            continue
        # Step berbasis blob: tulis hanya bila file belum ada / beda ukuran.
        try:
            if os.path.isfile(p) and os.path.getsize(p) == (a.get("size") or -1):
                continue
        except Exception:
            pass
        try:
            with open(p, "wb") as f:
                f.write(b)
        except Exception:
            pass


def save_step_rows(cfg, run, n, sheets_rows, headers_map, ext, download_name, summary,
                   edit_sheet=None):
    """Versi LAZY dari rowstore.save_step_rows: simpan BARIS + urutan header (meta)
    + record_step (blob NULL) ke DB, lalu HAPUS cache disk. TIDAK merakit Excel di
    sini — Excel dirakit on-demand saat diunduh (artifact_bytes) atau dipakai step
    berikutnya (load_state -> _materialize)."""
    mime = H.mime_for_ext(ext)
    conn = H._store()
    try:
        ds = H._resolve_dataset(conn, run, create=True)
        did = ds["id"]
        pstore.replace_step_rows(conn, did, int(n), sheets_rows)
        pstore.set_meta_value(conn, did, rowstore._headers_meta_key(n), headers_map or {})
        if edit_sheet is not None:
            pstore.set_meta_value(conn, did, rowstore._editsheet_meta_key(n), edit_sheet)
        pstore.record_step(conn, did, int(n), ext, download_name, mime, summary)
    finally:
        conn.close()
    _delete_disk_cache(cfg, run, n, ext)
    return {
        "status": "done",
        "file": "step%s.%s" % (n, ext),
        "name": download_name,
        "ext": ext,
        "mime": mime,
        "size": 0,
        "summary": summary,
        "at": _dt.datetime.now().isoformat(),
    }


def save_step_edits(cfg, run, n, items):
    """Versi LAZY dari rowstore.save_step_edits: UPSERT editan analis ke step_edit
    (kunci bisnis stabil) lalu HAPUS cache disk. TIDAK merakit Excel di sini."""
    ext = "xlsx"
    conn = H._store()
    try:
        ds = H._resolve_dataset(conn, run, create=True)
        did = ds["id"]
        pstore.upsert_edits(conn, did, int(n), items)
        meta = pstore.get_artifact_meta(conn, did, int(n))
        if meta and meta.get("ext"):
            ext = meta.get("ext")
    finally:
        conn.close()
    _delete_disk_cache(cfg, run, n, ext)
    return None


# Pasang override (late-binding global): load_state memanggil _materialize sebagai
# global modul helpers; save_step_from_xlsx memanggil save_step_rows sebagai global
# modul rowstore; step9_patch memanggil rowstore.save_step_rows/save_step_edits.
H._materialize = _materialize
rowstore.save_step_rows = save_step_rows
rowstore.save_step_edits = save_step_edits
print("[lazy_excel_patch] Excel dirakit LAZY (unduh / step berikutnya). "
      "_materialize=write-if-missing; save_step_rows/save_step_edits=hapus cache disk. "
      "Logika/format/rumus tidak berubah.", flush=True)
