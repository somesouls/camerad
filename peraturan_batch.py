# -*- coding: utf-8 -*-
"""
peraturan_batch.py
------------------
Impor massal satu folder berisi berkas peraturan (HTML TKB DJP / PDF) ke dalam
basis data Peraturan camerad.

Adaptasi dari jakai (app/batch.py). Perbedaan: jakai memisahkan korpus
'konten_teknis'; di camerad korpus itu SUDAH ditangani sumber #1-#3, jadi SEMUA
hasil di sini masuk ke satu tabel peraturan_unit.

Konvensi folder (opsional): nama subfolder dipakai sebagai petunjuk jenis,
misal:
    aturan/
      uu/      -> UU
      perpu/   -> PERPU
      pp/      -> PP
      pmk/     -> PMK
      perdjp/  -> PER
      (bebas)  -> jenis dideteksi dari isi berkas

Berkas 'lampiran' HTML dicocokkan ke peraturan induk melalui nomor yang
terdeteksi; bila tak ada induk, disimpan sebagai unit lampiran mandiri.

Hasil ditulis ke impor_log (bisa dilihat di UI). Baris berstatus 'perlu_ocr'
artinya PDF hasil scan tanpa lapisan teks -> jalankan ulang dengan do_ocr=True
(butuh biner tesseract 'ind' + poppler).
"""
import os
import re

import peraturan_files as F
import peraturan_parser as tkb_djp
import peraturan_db

_JENIS_DIR = {
    "uu": "UU", "perpu": "PERPU", "pp": "PP", "perpres": "PERPRES",
    "pmk": "PMK", "kmk": "KMK", "perdjp": "PER", "per": "PER",
    "kep": "KEP", "se": "SE",
}


def _jenis_dari_path(root, path):
    rel = os.path.relpath(path, root)
    parts = rel.replace("\\", "/").split("/")
    if len(parts) > 1:
        return _JENIS_DIR.get(parts[0].strip().lower())
    return None


def _key_dari_nama(nama):
    """Normalisasi nomor untuk mencocokkan lampiran dengan induk."""
    s = (nama or "").lower()
    s = re.sub(r"lampiran|\.html?|\.pdf|_|-", " ", s)
    s = re.sub(r"[^0-9a-z]+", "", s)
    return s


def _iter_files(root):
    for dirpath, _dirs, files in os.walk(root):
        for fn in sorted(files):
            if fn.lower().endswith((".html", ".htm", ".pdf")):
                yield os.path.join(dirpath, fn)


def proses(root, per_ayat=False, do_ocr=False, ingest=True, conn=None):
    """Proses seluruh folder `root`. Kembalikan ringkasan + daftar log.

    per_ayat : pecah unit per-ayat (default per-pasal).
    do_ocr   : OCR PDF scan (butuh tesseract+poppler); bila False, hanya ditandai.
    ingest   : bila True, tulis ke DB; bila False, hanya triase (dry-run).
    """
    if not root or not os.path.isdir(root):
        return {"ok": False, "error": "Folder tidak ditemukan: %s" % root, "log": []}

    own = conn is None
    conn = conn or peraturan_db.init_db(peraturan_db.connect())
    log = []
    ringkas = {"file": 0, "peraturan": 0, "unit": 0, "lampiran": 0,
              "perlu_ocr": 0, "gagal": 0, "lewati": 0}

    # index induk: key nomor -> {source_id, nomor, jenis}
    induk_index = {}
    lampiran_pending = []

    try:
        for path in _iter_files(root):
            ringkas["file"] += 1
            nama = os.path.basename(path)
            jenis_hint = _jenis_dari_path(root, path)
            row_log = {"file": os.path.relpath(path, root), "kategori": "peraturan",
                       "jenis": jenis_hint or "", "n_unit": 0}
            try:
                info = F.classify(path, do_ocr=do_ocr)
            except Exception as e:
                ringkas["gagal"] += 1
                row_log.update({"tipe": "unknown", "status": "gagal", "catatan": str(e)[:300]})
                log.append(row_log)
                continue

            row_log["tipe"] = info.tipe

            if info.tipe == "regulation_html":
                try:
                    html = open(path, encoding="utf-8", errors="replace").read()
                    meta, rows = tkb_djp.to_rows(html, per_ayat=per_ayat, jenis_hint=jenis_hint)
                except Exception as e:
                    ringkas["gagal"] += 1
                    row_log.update({"status": "gagal", "catatan": ("parse: " + str(e))[:300]})
                    log.append(row_log)
                    continue
                if not rows:
                    ringkas["lewati"] += 1
                    row_log.update({"status": "kosong", "catatan": "tidak ada unit terparse"})
                    log.append(row_log)
                    continue
                sid = meta.base_id
                for r in rows:
                    r["source_file"] = row_log["file"]
                    r["source_id"] = sid
                    if ingest:
                        peraturan_db.upsert_peraturan(r, conn=conn)
                induk_index[_key_dari_nama(meta.nomor) or _key_dari_nama(nama)] = {
                    "source_id": sid, "nomor": meta.nomor, "jenis": meta.jenis_peraturan,
                }
                ringkas["peraturan"] += 1
                ringkas["unit"] += len(rows)
                row_log.update({"source_id": sid, "nomor": meta.nomor,
                                "jenis": meta.jenis_peraturan, "n_unit": len(rows),
                                "status": "ok"})
                log.append(row_log)

            elif info.tipe == "lampiran_html":
                # tunda; cocokkan setelah semua induk terindeks
                lampiran_pending.append((path, row_log, info))

            elif info.tipe == "pdf_text":
                teks = (info.teks or "").strip()
                if len(re.sub(r"\s+", "", teks)) < 200:
                    ringkas["lewati"] += 1
                    row_log.update({"status": "kosong", "catatan": "teks PDF terlalu pendek"})
                    log.append(row_log)
                    continue
                sid = tkb_djp.slugify(info.nomor_teks or nama) or tkb_djp.slugify(nama)
                r = {
                    "id": "%s-utuh" % sid,
                    "jenis_peraturan": jenis_hint or "",
                    "nomor": info.nomor_teks or nama,
                    "judul": info.nomor_teks or nama,
                    "hierarchy": "(dokumen PDF - tanpa struktur Pasal)",
                    "isi": teks[:20000],
                    "status": "berlaku",
                    "can_cite": 1,
                    "source_file": row_log["file"],
                    "source_id": sid,
                }
                if ingest:
                    peraturan_db.upsert_peraturan(r, conn=conn)
                ringkas["peraturan"] += 1
                ringkas["unit"] += 1
                row_log.update({"source_id": sid, "nomor": r["nomor"], "n_unit": 1,
                                "status": "ok"})
                log.append(row_log)

            elif info.tipe == "pdf_scan":
                ringkas["perlu_ocr"] += 1
                row_log.update({"status": "perlu_ocr",
                                "catatan": "PDF scan; jalankan ulang dengan OCR (tesseract 'ind')"})
                log.append(row_log)

            else:
                ringkas["lewati"] += 1
                row_log.update({"status": "lewati", "catatan": info.catatan or "tipe tidak dikenal"})
                log.append(row_log)

        # ---- cocokkan lampiran ke induk ----
        for path, row_log, info in lampiran_pending:
            nama = os.path.basename(path)
            key = _key_dari_nama(info.nomor_teks or nama)
            induk = None
            for ik, iv in induk_index.items():
                if ik and (ik in key or key in ik) and len(ik) >= 4:
                    induk = iv
                    break
            teks = (info.teks or "").strip()
            if len(re.sub(r"\s+", "", teks)) < 60:
                ringkas["lewati"] += 1
                row_log.update({"kategori": "lampiran", "status": "kosong",
                                "catatan": "lampiran kosong"})
                log.append(row_log)
                continue
            sid = (induk or {}).get("source_id") or tkb_djp.slugify(info.nomor_teks or nama)
            lamp_id = "%s-lamp-%s" % (sid, tkb_djp.slugify(nama)[:40])
            r = {
                "id": lamp_id,
                "jenis_peraturan": (induk or {}).get("jenis") or row_log.get("jenis") or "",
                "nomor": (induk or {}).get("nomor") or info.nomor_teks or nama,
                "judul": "Lampiran - %s" % ((induk or {}).get("nomor") or nama),
                "lampiran": nama,
                "hierarchy": "Lampiran",
                "isi": teks[:20000],
                "status": "berlaku",
                "can_cite": 1,
                "source_file": row_log["file"],
                "source_id": sid,
            }
            if ingest:
                peraturan_db.upsert_peraturan(r, conn=conn)
            ringkas["lampiran"] += 1
            ringkas["unit"] += 1
            row_log.update({"kategori": "lampiran", "source_id": sid,
                            "nomor": r["nomor"], "n_unit": 1,
                            "status": "ok" if induk else "lampiran_mandiri"})
            log.append(row_log)

        if ingest and log:
            try:
                peraturan_db.upsert_impor_log(log, conn=conn)
            except Exception:
                pass

        return {"ok": True, "ringkas": ringkas, "log": log}
    finally:
        if own:
            conn.close()
