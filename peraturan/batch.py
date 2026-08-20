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

Fallback induk dari DB: bila induk HTML TIDAK ikut pada run yang sama (mis. Anda
sengaja memisahkan lampiran scan ke folder khusus OCR), lampiran yang punya teks
akan dicocokkan ke peraturan induk yang SUDAH diimpor lewat `source_id`
(peraturan_db.induk_info). Jadi lampiran itu tetap menempel ke induknya, bukan
tersimpan sebagai 'mandiri'. Bila di DB pun tak ada induk ber-source_id sama,
barulah disimpan mandiri.

== Link asli (source_url) ==
Nama berkas SEBENARNYA adalah URL halaman peraturan di TKB intranet yang sudah
disanitasi ('://'->'___', '/'->'_', '?'->'_'). `_url_dari_nama` merekonstruksi
URL asli itu sehingga peraturan bisa dibuka kembali lewat tautannya (memuat id).
Catatan: isi halaman diunduh dari endpoint render 'view/hasil.php', namun tautan
publik yang dibuka pengguna adalah 'view.php' (tanpa segmen '/hasil'), jadi URL
hasil rekonstruksi dinormalkan ke bentuk publik itu. Contoh:
    https___tkb-djp_tkb_engine_peraturan_view_hasil.php_id=0845...
 -> https://tkb-djp/tkb/engine/peraturan/view.php?id=0845...

== Cakupan folder & penamaan subfolder ==
Seluruh isi folder ditelusuri REKURSIF (os.walk), jadi cukup arahkan ke folder
induk (mis. .../aturan) untuk memproses semua subfolder sekaligus. Subfolder
tingkat-1 HANYA dipakai sebagai PETUNJUK jenis bila namanya termasuk daftar
yang dikenali (uu/pp/pmk/perpu/perpres/perdjp/perdirjen/per/kmk/kep/se). Nama
folder DI LUAR daftar itu (mis. 'ocr', 'scan', 'sisa') DIABAIKAN sebagai
petunjuk -> tidak akan menghasilkan 'jenis peraturan' baru; jenis tetap diambil
dari isi HTML (untuk peraturan) atau diwarisi dari induk (untuk lampiran). Jadi
aman menamai folder bebas seperti 'ocr'. Berkas yang langsung berada di folder
induk pun tetap diproses.

== Status & relasi peraturan ==
Parser (peraturan_parser) membaca status terkini dari HTML (berlaku/diubah/
dicabut) berikut daftar peraturan terkait pada kotak legenda_status (pengubah/
pencabut, peraturan TERBARU) dan legenda_history (peraturan SEBELUMNYA). Saat
impor folder, tautan relatif di daftar itu (view.php?id=<hash>) di-resolve
menjadi tautan absolut memakai URL asli peraturan induk, lalu disimpan sebagai
JSON pada kolom status_terkait / history_terkait. Dengan demikian menjalankan
ulang proses folder sekaligus MEMPERBAIKI status & melengkapi relasinya.

== Pemantauan progres (agar OCR tidak tampak 'diam') ==
Proses batch bisa lama, terutama saat OCR. Agar bisa dipantau:
  * Terminal: fungsi OCR (peraturan_files) mencetak progres per berkas & per
    halaman; loop batch mencetak heartbeat 'klasifikasi i/total' berkala.
  * UI: jalankan lewat `proses_async` (dipakai route) yang berjalan di thread
    latar sambil memperbarui state global; UI mem-poll `get_progress()` lewat
    endpoint /api/peraturan/batch-progress. State memuat fase, berkas i/total,
    jumlah berkas yang menjalani OCR + halaman berjalan, dan ringkasan akhir.

== Rekonsiliasi folder <-> DB (audit cakupan) ==
`audit_folder` menelusuri folder + subfolder lalu mencocokkan tiap berkas ke
basis data (lewat peraturan_db.list_sumber) TANPA menulis apa pun. Berguna saat
folder dianggap sudah lengkap untuk melacak berkas mana yang BELUM masuk DB
(tidak seperti log impor yang hanya menambah hitungan 'perlu OCR' saat gagal).
Status per berkas: 'ada' (nama berkas cocok di DB), 'induk_ada' (induk
ber-source_id sama sudah di DB tetapi berkas ini belum), 'belum' (sama sekali
belum ada), atau 'abaikan' (arsip/berkas sistem yang tak diimpor langsung).

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
import threading
import time

import peraturan.files as F
import peraturan.parser as tkb_djp
import peraturan.db as peraturan_db

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


def _log(msg):
    """Cetak heartbeat batch ke terminal (flush agar langsung terlihat)."""
    try:
        print("[peraturan][batch] " + msg, flush=True)
    except Exception:
        pass


# --------------------------------------------------------------- state progres
# State global dibaca UI lewat get_progress() (endpoint batch-progress).
_PROG_LOCK = threading.Lock()
_THREAD = None


def _progress_awal(root="", file_total=0, ocr_kandidat=0, do_ocr=False):
    return {
        "running": True,
        "phase": "mulai",          # mulai|klasifikasi|susun|selesai|gagal
        "root": root,
        "do_ocr": bool(do_ocr),
        "file_idx": 0,
        "file_total": int(file_total),
        "current": "",
        "ocr_total_kandidat": int(ocr_kandidat),   # perkiraan (semua PDF/gambar)
        "ocr_proses": 0,                            # berkas yang benar-benar di-OCR
        "ocr_ok": 0,                                # OCR yang menghasilkan teks
        "ocr_page": "",                             # halaman berjalan, mis '3/12'
        "ringkas": {},
        "started_at": time.time(),
        "updated_at": time.time(),
        "done": False,
        "ok": None,
        "error": "",
    }


_PROGRESS = {"running": False, "phase": "", "done": True, "ok": None,
             "file_idx": 0, "file_total": 0, "ocr_proses": 0, "ringkas": {}}


def reset_progress(root="", file_total=0, ocr_kandidat=0, do_ocr=False):
    with _PROG_LOCK:
        _PROGRESS.clear()
        _PROGRESS.update(_progress_awal(root, file_total, ocr_kandidat, do_ocr))


def _prog(**kw):
    with _PROG_LOCK:
        _PROGRESS.update(kw)
        _PROGRESS["updated_at"] = time.time()


def get_progress():
    """Snapshot state progres (aman untuk diserialisasi ke JSON)."""
    with _PROG_LOCK:
        return dict(_PROGRESS)


def is_running():
    with _PROG_LOCK:
        return bool(_PROGRESS.get("running"))


def _on_files_progress(event, data):
    """Callback dari peraturan_files saat OCR berjalan -> perbarui state."""
    with _PROG_LOCK:
        if event in ("ocr_pdf_start", "ocr_image_start"):
            _PROGRESS["ocr_proses"] = _PROGRESS.get("ocr_proses", 0) + 1
            _PROGRESS["ocr_page"] = ""
        elif event == "ocr_page":
            _PROGRESS["ocr_page"] = "%s/%s" % (data.get("i"), data.get("n"))
        elif event in ("ocr_pdf_done", "ocr_image_done"):
            if (data.get("chars") or 0) > 0:
                _PROGRESS["ocr_ok"] = _PROGRESS.get("ocr_ok", 0) + 1
            _PROGRESS["ocr_page"] = ""
        _PROGRESS["updated_at"] = time.time()


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

    Endpoint render 'view/hasil.php' dinormalkan ke tautan publik 'view.php'
    (tanpa '/hasil'):
        https___tkb-djp_tkb_engine_peraturan_view_hasil.php_id=0845...
     -> https://tkb-djp/tkb/engine/peraturan/view.php?id=0845...
    """
    name = os.path.splitext(os.path.basename(path))[0]
    if name.endswith("_lampiran"):
        name = name[: -len("_lampiran")]
    if "://" in name:                         # sudah berupa URL utuh
        return _normalkan_url(name)
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
    return _normalkan_url(url)


def _normalkan_url(url):
    """Samakan endpoint render TKB 'view/hasil.php' -> tautan publik 'view.php'."""
    return url.replace("/view/hasil.php", "/view.php")


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
    """Baris peraturan_unit untuk sebuah LAMPIRAN, tertaut ke peraturan induk.

    Lampiran mewarisi status + relasi (status_terkait/history_terkait) dari
    peraturan induknya, dengan tautan relasi di-resolve terhadap URL induk.
    """
    kekuatan = getattr(tkb_djp, "_KEKUATAN", {}).get(meta.jenis_peraturan, 50)
    rid = _uniq_id("%s-lampiran" % meta.base_id, dipakai)
    base_url = url or meta.source_url
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
        "status": getattr(meta, "status", "berlaku") or "berlaku",
        "valid_from": meta.valid_from,
        "kekuatan_hukum": kekuatan,
        "can_cite": 1,
        "source_url": url or meta.source_url,
        "source_file": rel,
        "source_id": sid,
        "status_terkait": tkb_djp.related_json(getattr(meta, "status_terkait", []), base_url),
        "history_terkait": tkb_djp.related_json(getattr(meta, "history_terkait", []), base_url),
    }


def _baris_lampiran_db(dbinfo, teks, nama, rel, sid, url, dipakai):
    """Seperti _baris_lampiran, tetapi identitas induk diambil dari DB
    (peraturan_db.induk_info) berdasarkan source_id. Dipakai saat lampiran
    diproses pada run TERPISAH (mis. folder khusus OCR) sementara induk HTML
    tidak ikut di folder itu. Nilai status_terkait/history_terkait dari DB
    sudah berupa JSON string siap pakai, jadi dibawa apa adanya.
    """
    jenis = dbinfo.get("jenis_peraturan")
    kekuatan = getattr(tkb_djp, "_KEKUATAN", {}).get(jenis, 50)
    base = dbinfo.get("id") or sid or "unit"
    rid = _uniq_id("%s-lampiran" % base, dipakai)
    return {
        "id": rid,
        "jenis_peraturan": jenis,
        "nomor": dbinfo.get("nomor"),
        "tahun": dbinfo.get("tahun"),
        "judul": dbinfo.get("judul"),
        "pasal": None,
        "ayat": None,
        "lampiran": nama,
        "isi": (teks or "")[:20000],
        "hierarchy": ("%s %s > Lampiran" % (jenis or "", dbinfo.get("nomor") or "")).strip(),
        "status": dbinfo.get("status") or "berlaku",
        "valid_from": dbinfo.get("valid_from"),
        "kekuatan_hukum": kekuatan,
        "can_cite": 1,
        "source_url": url or dbinfo.get("source_url"),
        "source_file": rel,
        "source_id": sid,
        "status_terkait": dbinfo.get("status_terkait"),
        "history_terkait": dbinfo.get("history_terkait"),
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

    Selama berjalan, state progres global diperbarui (lihat get_progress) dan
    heartbeat dicetak ke terminal.
    """
    if not root or not os.path.isdir(root):
        _prog(running=False, done=True, ok=False, phase="gagal",
              error="Folder tidak ditemukan: %s" % root)
        return {"ok": False, "error": "Folder tidak ditemukan: %s" % root, "log": []}

    own = conn is None
    conn = conn or peraturan_db.init_db(peraturan_db.connect())
    log = []
    ringkas = {"file": 0, "peraturan": 0, "unit": 0, "lampiran": 0,
               "perlu_ocr": 0, "perlu_perhatian": 0, "gagal": 0, "lewati": 0}
    ids_pakai = set()

    def _induk_db(sid):
        """Cari induk di DB berdasarkan source_id (hanya saat ingest)."""
        if not (ingest and sid):
            return None
        try:
            return peraturan_db.induk_info(sid, conn=conn)
        except Exception:
            return None

    F.set_progress_cb(_on_files_progress)
    try:
        files = list(_iter_files(root))
        total = len(files)
        ocr_kandidat = sum(
            1 for p in files
            if p.lower().endswith(".pdf") or p.lower().endswith(F.IMG_EXT)
        )
        reset_progress(root=root, file_total=total, ocr_kandidat=ocr_kandidat, do_ocr=do_ocr)
        _log("mulai: %s | berkas=%d | kandidat OCR=%d | ocr=%s | ingest=%s"
             % (root, total, ocr_kandidat, do_ocr, ingest))

        # ---------- Fase 1: klasifikasi semua berkas + parse peraturan HTML ----------
        _prog(phase="klasifikasi")
        items = []          # (path, rel, kat, info, err)
        parsed = {}         # path -> (meta, rows) ; meta None => gagal parse (rows=pesan)
        induk_by_key = {}   # key nama -> meta induk

        for idx, path in enumerate(files, start=1):
            ringkas["file"] += 1
            rel = os.path.relpath(path, root)
            _prog(file_idx=idx, current=rel)
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
            if idx % 25 == 0 or idx == total:
                snap = get_progress()
                _log("klasifikasi %d/%d | OCR diproses=%d/%d"
                     % (idx, total, snap.get("ocr_proses", 0), ocr_kandidat))

        # ---------- Fase 2: susun keluaran ----------
        _prog(phase="susun")
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
                st_json = tkb_djp.related_json(meta.status_terkait, url or meta.source_url)
                hs_json = tkb_djp.related_json(meta.history_terkait, url or meta.source_url)
                for r in rows:
                    r["id"] = _uniq_id(r.get("id") or (meta.base_id + "-x"), ids_pakai)
                    r["source_file"] = rel
                    r["source_id"] = sid
                    if url:
                        r["source_url"] = url
                    # perbaiki status dari HTML + lengkapi relasi (tautan absolut)
                    r["status"] = getattr(meta, "status", "berlaku") or "berlaku"
                    r["status_terkait"] = st_json
                    r["history_terkait"] = hs_json
                    if ingest:
                        peraturan_db.upsert_peraturan(r, conn=conn)
                ringkas["peraturan"] += 1
                ringkas["unit"] += len(rows)
                catatan_status = "status: %s" % (getattr(meta, "status", "berlaku") or "berlaku")
                n_rel = len(meta.status_terkait or []) + len(meta.history_terkait or [])
                if n_rel:
                    catatan_status += "; relasi: %d" % n_rel
                row.update(jenis=meta.jenis_peraturan, nomor=meta.nomor,
                           n_unit=len(rows), status="ok", catatan=catatan_status)
                log.append(row)
                continue

            # --- Kandidat lampiran/dokumen: cocokkan ke induk lewat NAMA BERKAS ---
            induk = induk_by_key.get(_key_dari_nama(path))

            # scan tanpa lapisan teks (OCR tak jalan / tak tersedia)
            if info.tipe in ("pdf_scan", "image_scan"):
                ringkas["perlu_ocr"] += 1
                dbinduk = _induk_db(sid) if induk is None else None
                if induk is not None:
                    row["kategori"] = "lampiran"
                    row.update(jenis=induk.jenis_peraturan, nomor=induk.nomor,
                               status="perlu_ocr",
                               catatan="lampiran (scan) dari %s %s; aktifkan OCR"
                                       % (induk.jenis_peraturan, induk.nomor))
                elif dbinduk is not None:
                    row["kategori"] = "lampiran"
                    row.update(jenis=dbinduk.get("jenis_peraturan"), nomor=dbinduk.get("nomor"),
                               status="perlu_ocr",
                               catatan="lampiran (scan) dari %s %s (induk dari DB); aktifkan OCR"
                                       % (dbinduk.get("jenis_peraturan") or "", dbinduk.get("nomor") or ""))
                else:
                    row["kategori"] = "dokumen"
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
                    log.append(row)
                    continue
                dbinduk = _induk_db(sid)
                if dbinduk is not None:
                    r = _baris_lampiran_db(dbinduk, teks, nama, rel, sid, url, ids_pakai)
                    if ingest:
                        peraturan_db.upsert_peraturan(r, conn=conn)
                    ringkas["lampiran"] += 1
                    ringkas["unit"] += 1
                    row.update(kategori="lampiran", jenis=dbinduk.get("jenis_peraturan"),
                               nomor=dbinduk.get("nomor"), n_unit=1, status="ok",
                               catatan="lampiran -> %s %s (induk dari DB)"
                                       % (dbinduk.get("jenis_peraturan") or "", dbinduk.get("nomor") or ""))
                    log.append(row)
                    continue
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

        _prog(phase="selesai", ringkas=dict(ringkas), running=False, done=True, ok=True)
        _log("selesai: %s" % ringkas)
        return {"ok": True, "ringkas": ringkas, "log": log}
    except Exception as e:
        _prog(phase="gagal", ringkas=dict(ringkas), running=False, done=True,
              ok=False, error=str(e)[:400])
        _log("GAGAL: %s" % str(e)[:200])
        return {"ok": False, "error": str(e), "ringkas": ringkas, "log": log}
    finally:
        F.set_progress_cb(None)
        if own:
            conn.close()


def proses_async(root, per_ayat=False, do_ocr=False, ingest=True):
    """Jalankan proses(...) di thread latar agar UI bisa mem-poll get_progress().

    Kembalikan segera: {ok, started} atau {ok:false, error/running}. Hanya satu
    batch boleh berjalan pada satu waktu.
    """
    global _THREAD
    if not root or not os.path.isdir(root):
        return {"ok": False, "error": "Folder tidak ditemukan: %s" % root}
    with _PROG_LOCK:
        if _PROGRESS.get("running"):
            return {"ok": False, "running": True,
                    "error": "Batch masih berjalan. Tunggu hingga selesai."}
    # Tandai running lebih awal supaya polling langsung mendapati status berjalan.
    reset_progress(root=root, file_total=0, ocr_kandidat=0, do_ocr=do_ocr)

    def _run():
        proses(root, per_ayat=per_ayat, do_ocr=do_ocr, ingest=ingest)

    _THREAD = threading.Thread(target=_run, name="peraturan-batch", daemon=True)
    _THREAD.start()
    return {"ok": True, "started": True}


# ------------------------------------------------------- rekonsiliasi (audit)
def _tipe_hint(nama):
    """Tebak kategori & tipe ringkas SEBUAH berkas dari nama/ekstensi saja
    (tanpa membaca isi), untuk keperluan audit yang harus cepat.

    Kembalikan (kategori, tipe): kategori in peraturan|lampiran|arsip|lain;
    tipe = 'html'|'pdf'|'gambar'|'arsip'|'skip'|<ext>.
    """
    low = nama.lower()
    ext = os.path.splitext(low)[1]
    img = getattr(F, "IMG_EXT", (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"))
    ars = getattr(F, "ARSIP_EXT", (".zip", ".rar", ".7z", ".tar", ".gz"))
    skip = getattr(F, "SKIP_NAMES", set())
    if low in skip:
        return "lain", "skip"
    if ext in (".html", ".htm"):
        base = os.path.splitext(low)[0]
        return ("lampiran", "html") if base.endswith("_lampiran") else ("peraturan", "html")
    if ext == ".pdf":
        return "lampiran", "pdf"
    if ext in img:
        return "lampiran", "gambar"
    if ext in ars:
        return "arsip", "arsip"
    return "lain", (ext.lstrip(".") or "?")


def audit_folder(root, status_filter="", limit=5000, conn=None):
    """Rekonsiliasi folder <-> DB: telusuri folder + subfolder, cocokkan tiap
    berkas ke basis data TANPA menulis apa pun. Berguna untuk melacak berkas
    yang BELUM masuk DB saat folder dianggap sudah lengkap.

    status_filter : '' (semua) | 'belum' | 'induk_ada' | 'ada' | 'abaikan'.
    limit         : batas jumlah baris yang dikembalikan (ringkasan tetap penuh).

    Cara cocok:
      * 'ada'       : basename berkas cocok dengan source_file di DB.
      * 'induk_ada' : basename tak cocok, tetapi source_id (id=<hash> pada nama)
                      sudah ada di DB -> induk peraturan ada, berkas ini belum.
      * 'belum'     : tak ada kecocokan sama sekali.
      * 'abaikan'   : arsip (zip/rar/...) atau berkas sistem -> tak diimpor langsung.

    Kembalikan {ok, ringkas:{total,ada,induk_ada,belum,abaikan}, rows[...],
    ditampilkan, limit}. Tiap row: file(rel), nama, kategori, tipe, jenis,
    nomor, judul, source_id, status, keterangan.
    """
    if not root or not os.path.isdir(root):
        return {"ok": False, "error": "Folder tidak ditemukan: %s" % root}
    own = conn is None
    conn = conn or peraturan_db.init_db(peraturan_db.connect())
    try:
        # Peta sumber terindeks: per basename & per source_id.
        by_file = {}
        by_sid = {}
        try:
            for r in peraturan_db.list_sumber(conn=conn):
                sf = r.get("source_file")
                if sf:
                    bn = os.path.basename(sf).lower()
                    by_file.setdefault(bn, r)
                sid = r.get("source_id")
                if sid:
                    cur = by_sid.get(sid)
                    # Utamakan baris NON-lampiran sebagai perwakilan induk.
                    if cur is None or (cur.get("is_lampiran") and not r.get("is_lampiran")):
                        by_sid[sid] = r
        except Exception as e:
            return {"ok": False, "error": "Gagal membaca DB: %s" % str(e)[:200]}

        ringkas = {"total": 0, "ada": 0, "induk_ada": 0, "belum": 0, "abaikan": 0}
        rows = []
        for path in _iter_files(root):
            nama = os.path.basename(path)
            rel = os.path.relpath(path, root)
            low = nama.lower()
            kategori, tipe = _tipe_hint(nama)
            sid = _source_id(path)

            jenis = nomor = judul = ""
            dbrow = by_file.get(low)
            if dbrow is not None:
                status = "ada"
                jenis = dbrow.get("jenis_peraturan") or ""
                nomor = dbrow.get("nomor") or ""
                judul = dbrow.get("judul") or ""
                ket = "Tercatat di database"
            else:
                indb = by_sid.get(sid)
                if indb is not None:
                    status = "induk_ada"
                    jenis = indb.get("jenis_peraturan") or ""
                    nomor = indb.get("nomor") or ""
                    judul = indb.get("judul") or ""
                    ket = "Induk peraturan sudah di DB; berkas ini belum tercatat"
                else:
                    status = "belum"
                    ket = "Belum ada di database"

            # Arsip & berkas sistem: tandai 'abaikan' (kecuali memang sudah 'ada').
            if tipe == "skip" and status != "ada":
                status, ket = "abaikan", "Berkas sistem — dilewati"
            elif kategori == "arsip" and status != "ada":
                status, ket = "abaikan", "Arsip — ekstrak dulu, tidak diimpor langsung"

            ringkas["total"] += 1
            ringkas[status] = ringkas.get(status, 0) + 1
            if (not status_filter) or status_filter == status:
                if len(rows) < limit:
                    rows.append({
                        "file": rel, "nama": nama, "kategori": kategori, "tipe": tipe,
                        "jenis": jenis, "nomor": nomor, "judul": judul,
                        "source_id": sid, "status": status, "keterangan": ket,
                    })
        # Urutkan: berkas yang lebih perlu perhatian di atas.
        prioritas = {"belum": 0, "induk_ada": 1, "abaikan": 2, "ada": 3}
        rows.sort(key=lambda x: (prioritas.get(x["status"], 9), x["file"]))
        return {"ok": True, "ringkas": ringkas, "rows": rows,
                "ditampilkan": len(rows), "limit": limit}
    finally:
        if own:
            conn.close()
