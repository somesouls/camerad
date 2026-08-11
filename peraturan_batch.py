# -*- coding: utf-8 -*-
"""
peraturan_batch.py
------------------
Impor massal satu folder berisi berkas peraturan + lampiran ke basis data
Peraturan camerad. PORT SETIA dari jakai (app/batch.py), diadaptasi: camerad
tidak punya tabel 'konten_teknis', jadi SEMUA hasil (peraturan, lampiran, dan
dokumen mandiri) masuk ke satu tabel peraturan_unit.

== Pencocokan lampiran <-> induk (INTI) ==
Sama seperti jakai: dicocokkan lewat NAMA BERKAS, bukan nomor hasil parsing.
`_key_dari_nama` membuang ekstensi lalu membuang akhiran '_lampiran', sehingga
unduhan TKB DJP berikut menghasilkan kunci sama:
    <nama>.html            -> peraturan induk (HTML)
    <nama>.pdf             -> lampiran PDF
    <nama>_lampiran(.html) -> lampiran HTML (bisa tanpa ekstensi)
Selain itu `source_id` diambil dari pola 'id=<hash>' pada nama berkas, sehingga
induk & lampiran berbagi source_id (memudahkan pengelolaan status per peraturan).

== Link asli (source_url) ==
Nama berkas SEBENARNYA adalah URL halaman peraturan di TKB intranet yang sudah
disanitasi ('://'->'___', '/'->'_', '?'->'_'). `_url_dari_nama` merekonstruksi
URL asli itu sehingga peraturan bisa dibuka kembali lewat tautannya (memuat id).
Contoh:
    https___tkb-djp_tkb_engine_peraturan_view_hasil.php_id=1b61...
 -> https://tkb-djp/tkb/engine/peraturan/view/hasil.php?id=1b61...

== Konvensi subfolder (opsional, sebagai petunjuk jenis) ==
    aturan/uu/  -> UU   aturan/pp/  -> PP   aturan/pmk/ -> PMK
    aturan/perpu/ perpres/ perdjp(->PER)/ kmk/ kep/ se/
Bila folder yang diproses langsung berisi berkas (mis. .../aturan/uu), jenis
dideteksi dari isi HTML.

== Triase (kolom status pada impor_log & ringkasan) ==
    ok              : peraturan/lampiran tersimpan
    kosong          : dibaca tapi tak ada isi berarti -> DILEWATI
    mandiri         : lampiran/dokumen tanpa induk -> tetap disimpan
    perlu_ocr       : PDF/gambar hasil scan tanpa lapisan teks -> aktifkan OCR
    perlu_perhatian : zip/format lain -> tangani manual
    gagal           : error saat klasifikasi/parse

OCR: aktifkan do_ocr=True (butuh biner tesseract 'ind' + poppler). Bila aktif &
tersedia, PDF/gambar scan langsung di-OCR saat impor; bila tidak, hanya ditandai.
"""
import os
import re

import peraturan_files as F
import peraturan_parser as tkb_djp
import peraturan_db

_JENIS_DIR = {
    "uu": "UU", "perpu": "PERPU", "pp": "PP", "perpres": "PERPRES",
    "pmk": "PMK", "kmk": "KMK", "perdjp": "PER", "perdirjen": "PER",
    "per": "PER", "kep": "KEP", "se": "SE",
}
_JENIS_VALID = set(_JENIS_DIR.values())

# Cocokkan id=<hash> pada nama berkas/URL TKB DJP, mis.
#   https___tkb-djp..._id=1b6171ff276542bd344c1600aaca6165.pdf
_RE_SOURCE_ID = re.compile(r"id[=_\-]([0-9a-fA-F]{8,})")

# Nama berkas berupa URL yang disanitasi diawali 'https___' / 'http___'.
_RE_SCHEME = re.compile(r"^(https?)___")


def kategori_dari_path(root, path):
    """Nama subfolder tingkat-1 di bawah root -> kode jenis (atau '')."""
    rel = os.path.relpath(path, root)
    bagian = rel.replace("\\", "/").split("/")
    top = bagian[0].lower() if len(bagian) > 1 else ""
    if not top:
        return ""
    return _JENIS_DIR.get(top, top.upper())


def _key_dari_nama(path):
    """Kunci pencocokan lampiran<->induk dari NAMA BERKAS.

    - buang ekstensi
    - buang akhiran '_lampiran' (varian HTML lampiran, termasuk tanpa ekstensi)
    """
    name = os.path.splitext(os.path.basename(path))[0].lower()
    if name.endswith("_lampiran"):
        name = name[: -len("_lampiran")]
    return name


def _source_id(path):
    """Ambil hash 'id=<hash>' dari nama berkas; fallback ke kunci nama.

    Membuat lampiran & peraturan induk berbagi source_id yang sama.
    """
    name = os.path.basename(path)
    m = _RE_SOURCE_ID.search(name)
    if m:
        return m.group(1).lower()
    return _key_dari_nama(path)


def _url_dari_nama(path):
    """Rekonstruksi URL asli TKB dari nama berkas (yang memang berupa URL).

    Nama unduhan = URL yang disanitasi: '://' -> '___', '/' -> '_', '?' -> '_'
    (karakter '=' dan '.' dipertahankan). Akhiran '_lampiran' dibuang agar
    lampiran menunjuk ke halaman peraturan induknya. Kembalikan '' bila pola
    tak dikenali (mis. HTML tempelan manual tanpa nama-URL).

        https___tkb-djp_tkb_engine_peraturan_view_hasil.php_id=1b61...
     -> https://tkb-djp/tkb/engine/peraturan/view/hasil.php?id=1b61...
    """
    name = os.path.splitext(os.path.basename(path))[0]
    if name.endswith("_lampiran"):
        name = name[: -len("_lampiran")]
    if "://" in name:                         # sudah berupa URL utuh
        return name
    m = _RE_SCHEME.match(name)
    if not m:
        return ""
    scheme = m.group(1)
    rest = name[len(scheme) + 3:]             # buang 'scheme___'
    qsep = rest.find("_id=")                   # '?' menjadi '_' tepat sebelum 'id='
    if qsep >= 0:
        path_part, query = rest[:qsep], rest[qsep + 1:]
    else:
        path_part, query = rest, ""
    url = "%s://%s" % (scheme, path_part.replace("_", "/"))
    if query:
        url += "?" + query
    return url


def _iter_files(root):
    """Semua berkas (bukan hanya HTML/PDF) supaya format lain ikut ditriase."""
    for dirpath, _dirs, files in os.walk(root):
        for fn in sorted(files):
            if fn.startswith("."):
                continue
            yield os.path.join(dirpath, fn)


def _uniq_id(base, dipakai):
    rid = base or "unit"
    i = 1
    while rid in dipakai:
        i += 1
        rid = "%s-%d" % (base, i)
    dipakai.add(rid)
    return rid


def _baris_lampiran(meta, teks, nama, rel, sid, url, dipakai):
    """Baris peraturan_unit untuk sebuah LAMPIRAN, tertaut ke peraturan induk."""
    kekuatan = getattr(tkb_djp, "_KEKUATAN", {}).get(meta.jenis_peraturan, 50)
    rid = _uniq_id("%s-lampiran" % meta.base_id, dipakai)
    return {
        "id": rid,
        "jenis_peraturan": meta.jenis_peraturan,
        "nomor": meta.nomor,
        "tahun": meta.tahun,
        "judul": meta.judul,
        "pasal": None,
        "ayat": None,
        "lampiran": nama,
        "isi": (teks or "")[:20000],
        "hierarchy": ("%s %s > Lampiran" % (meta.jenis_peraturan, meta.nomor)).strip(),
        "status": "berlaku",
        "valid_from": meta.valid_from,
        "kekuatan_hukum": kekuatan,
        "can_cite": 1,
        "source_url": url or meta.source_url,
        "source_file": rel,
        "source_id": sid,
    }


def _baris_mandiri(info, jenis_hint, nama, rel, sid, url, dipakai, lampiran=False):
    """Baris peraturan_unit untuk lampiran/dokumen TANPA induk (disimpan mandiri)."""
    dasar = info.nomor_teks or os.path.splitext(nama)[0]
    rid = _uniq_id(tkb_djp.slugify(dasar) or "dok", dipakai)
    return {
        "id": rid,
        "jenis_peraturan": jenis_hint or "",
        "nomor": info.nomor_teks or nama,
        "judul": ("Lampiran - %s" % dasar) if lampiran else (info.nomor_teks or nama),
        "lampiran": nama if lampiran else None,
        "isi": (info.teks or "")[:20000],
        "hierarchy": "Lampiran (tanpa induk)" if lampiran
                     else "(dokumen mandiri - tanpa struktur Pasal)",
        "status": "berlaku",
        "can_cite": 1,
        "source_url": url or None,
        "source_file": rel,
        "source_id": sid,
    }


def proses(root, per_ayat=False, do_ocr=False, ingest=True, conn=None):
    """Proses seluruh folder `root`. Kembalikan ringkasan + daftar log.

    per_ayat : pecah unit per-ayat (default per-pasal).
    do_ocr   : OCR PDF/gambar scan (butuh tesseract+poppler); bila False ditandai.
    ingest   : bila True tulis ke DB; bila False hanya triase (uji coba).
    """
    if not root or not os.path.isdir(root):
        return {"ok": False, "error": "Folder tidak ditemukan: %s" % root, "log": []}

    own = conn is None
    conn = conn or peraturan_db.init_db(peraturan_db.connect())
    log = []
    ringkas = {"file": 0, "peraturan": 0, "unit": 0, "lampiran": 0,
               "perlu_ocr": 0, "perlu_perhatian": 0, "gagal": 0, "lewati": 0}
    ids_pakai = set()

    try:
        # ---------- Fase 1: klasifikasi semua berkas + parse peraturan HTML ----------
        items = []          # (path, rel, kat, info, err)
        parsed = {}         # path -> (meta, rows) ; meta None => gagal parse (rows=pesan)
        induk_by_key = {}   # key nama -> meta induk

        for path in _iter_files(root):
            ringkas["file"] += 1
            rel = os.path.relpath(path, root)
            kat = kategori_dari_path(root, path)
            try:
                info = F.classify(path, do_ocr=do_ocr)
            except Exception as e:
                items.append((path, rel, kat, None, str(e)[:300]))
                continue
            items.append((path, rel, kat, info, None))
            if info.tipe == "regulation_html":
                jenis_hint = kat if kat in _JENIS_VALID else None
                try:
                    html = open(path, encoding="utf-8", errors="replace").read()
                    meta, rows = tkb_djp.to_rows(html, per_ayat=per_ayat, jenis_hint=jenis_hint)
                    parsed[path] = (meta, rows)
                    induk_by_key[_key_dari_nama(path)] = meta
                except Exception as e:
                    parsed[path] = (None, str(e))

        # ---------- Fase 2: susun keluaran ----------
        for path, rel, kat, info, err in items:
            nama = os.path.basename(path)
            sid = _source_id(path)
            url = _url_dari_nama(path)
            row = {"file": rel, "source_id": sid, "kategori": "peraturan",
                   "jenis": (kat if kat in _JENIS_VALID else ""), "tipe": "",
                   "nomor": "", "n_unit": 0, "status": "", "catatan": ""}

            if err is not None:
                ringkas["gagal"] += 1
                row.update(tipe="error", status="gagal", catatan=("klasifikasi: " + err)[:300])
                log.append(row)
                continue

            row["tipe"] = info.tipe

            # --- Peraturan HTML ---
            if info.tipe == "regulation_html":
                meta, rows = parsed.get(path, (None, None))
                if meta is None:
                    ringkas["gagal"] += 1
                    row.update(status="gagal", catatan=("parse: " + str(rows))[:300])
                    log.append(row)
                    continue
                if not rows:
                    ringkas["lewati"] += 1
                    row.update(jenis=meta.jenis_peraturan, nomor=meta.nomor,
                               status="kosong", catatan="tak ada pasal terdeteksi")
                    log.append(row)
                    continue
                for r in rows:
                    r["id"] = _uniq_id(r.get("id") or (meta.base_id + "-x"), ids_pakai)
                    r["source_file"] = rel
                    r["source_id"] = sid
                    if url:
                        r["source_url"] = url
                    if ingest:
                        peraturan_db.upsert_peraturan(r, conn=conn)
                ringkas["peraturan"] += 1
                ringkas["unit"] += len(rows)
                row.update(jenis=meta.jenis_peraturan, nomor=meta.nomor,
                           n_unit=len(rows), status="ok")
                log.append(row)
                continue

            # --- Kandidat lampiran/dokumen: cocokkan ke induk lewat NAMA BERKAS ---
            induk = induk_by_key.get(_key_dari_nama(path))

            # scan tanpa lapisan teks (OCR tak jalan / tak tersedia)
            if info.tipe in ("pdf_scan", "image_scan"):
                ringkas["perlu_ocr"] += 1
                row["kategori"] = "lampiran" if induk is not None else "dokumen"
                if induk is not None:
                    row.update(jenis=induk.jenis_peraturan, nomor=induk.nomor,
                               status="perlu_ocr",
                               catatan="lampiran (scan) dari %s %s; aktifkan OCR"
                                       % (induk.jenis_peraturan, induk.nomor))
                else:
                    row.update(status="perlu_ocr",
                               catatan=(info.catatan or ("%d hal tanpa lapisan teks; aktifkan OCR"
                                                         % (info.n_halaman or 0))))
                log.append(row)
                continue

            # lampiran/dokumen dengan teks (html / pdf berteks / gambar hasil OCR)
            if info.tipe in ("lampiran_html", "pdf_text", "image_text"):
                teks = (info.teks or "").strip()
                if len(re.sub(r"\s+", "", teks)) < 40:
                    ringkas["lewati"] += 1
                    row.update(kategori="lampiran", status="kosong",
                               catatan="teks lampiran terlalu pendek")
                    log.append(row)
                    continue
                if induk is not None:
                    r = _baris_lampiran(induk, teks, nama, rel, sid, url, ids_pakai)
                    if ingest:
                        peraturan_db.upsert_peraturan(r, conn=conn)
                    ringkas["lampiran"] += 1
                    ringkas["unit"] += 1
                    row.update(kategori="lampiran", jenis=induk.jenis_peraturan,
                               nomor=induk.nomor, n_unit=1, status="ok",
                               catatan="lampiran -> %s %s" % (induk.jenis_peraturan, induk.nomor))
                else:
                    is_lamp = info.tipe == "lampiran_html"
                    r = _baris_mandiri(info, (kat if kat in _JENIS_VALID else ""),
                                       nama, rel, sid, url, ids_pakai, lampiran=is_lamp)
                    if ingest:
                        peraturan_db.upsert_peraturan(r, conn=conn)
                    if is_lamp:
                        ringkas["lampiran"] += 1
                    else:
                        ringkas["peraturan"] += 1
                    ringkas["unit"] += 1
                    row.update(kategori="lampiran" if is_lamp else "dokumen",
                               nomor=info.nomor_teks, n_unit=1, status="mandiri",
                               catatan="tanpa induk; disimpan mandiri")
                log.append(row)
                continue

            # arsip / format lain -> perlu perhatian manual
            if info.tipe in ("arsip", "unknown"):
                ringkas["perlu_perhatian"] += 1
                row["kategori"] = "lampiran" if induk is not None else "lain"
                cat = info.catatan or "format belum didukung"
                if induk is not None:
                    row.update(jenis=induk.jenis_peraturan, nomor=induk.nomor)
                    cat += " (milik %s %s)" % (induk.jenis_peraturan, induk.nomor)
                row.update(status="perlu_perhatian", catatan=cat[:300])
                log.append(row)
                continue

            # fallback
            ringkas["lewati"] += 1
            row.update(status="lewati", catatan=info.catatan or "tipe tak dikenal")
            log.append(row)

        if ingest and log:
            try:
                peraturan_db.upsert_impor_log(log, conn=conn)
            except Exception:
                pass

        return {"ok": True, "ringkas": ringkas, "log": log}
    finally:
        if own:
            conn.close()
