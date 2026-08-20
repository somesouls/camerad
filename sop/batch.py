# -*- coding: utf-8 -*-
"""sop_batch.py — Impor massal folder SOP/Proses Bisnis ke basis data.

Alur:
  1. Telusuri folder + sub-folder, pilih berkas berformat didukung.
  2. Ekstrak teks + bagian (sop_files.classify), OCR bila diminta.
  3. Tentukan JUDUL & KATEGORI secara HYBRID:
       - pakai kandidat aturan (metadata/heading/nama berkas) bila sudah bermakna;
       - bila kurang bermakna DAN use_ai_naming aktif, lempar cuplikan ke LLM
         (llm_client.chat) untuk usul {judul, kategori}.
  4. Simpan PERMANEN: tiap bagian -> satu unit sop_db.upsert_sop (embedding e5
     otomatis) sehingga langsung dipakai mesin RAG (Sumber 5).
  5. Catat hasil ke sop_impor_log.

Progres real-time (state modul) bisa dipantau UI lewat get_progress(), termasuk
progres OCR yang diteruskan dari peraturan_files.set_progress_cb — sama seperti
monitor OCR menu Peraturan.

Juga menyediakan audit_folder(): rekonsiliasi berkas folder vs isi DB (berkas
mana yang BELUM masuk basis data).
"""
import os
import re
import threading
import traceback

import sop_files as SF
import sop_db as sdb
import peraturan_files as PF

try:
    import llm_client
except Exception:
    llm_client = None

_TAG = "[sop][batch] "
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def upload_dir():
    """Folder tujuan berkas hasil unggah (dibuat bila belum ada)."""
    d = os.environ.get("PIPELINE_SOP_UPLOAD_DIR") or os.path.join(_BASE_DIR, "sop_uploads")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


# --------------------------------------------------------------- state progres
_PROG_LOCK = threading.Lock()
_THREAD = None


def _progress_awal():
    return {
        "running": False,
        "fase": "idle",       # idle | mulai | proses | ocr | selesai | error
        "root": "",
        "total": 0,
        "selesai": 0,
        "file": "",
        "judul": "",
        "n_unit": 0,
        "ditambah": 0,
        "dilewati": 0,
        "gagal": 0,
        "perlu_ocr": 0,
        "ocr": {},          # detail progres OCR berkas berjalan
        "pesan": "",
        "error": "",
    }


_PROGRESS = _progress_awal()


def reset_progress():
    global _PROGRESS
    with _PROG_LOCK:
        _PROGRESS = _progress_awal()


def _prog(**kw):
    with _PROG_LOCK:
        _PROGRESS.update(kw)


def get_progress():
    with _PROG_LOCK:
        return dict(_PROGRESS)


def is_running():
    with _PROG_LOCK:
        return bool(_PROGRESS.get("running"))


def _on_files_progress(event, data):
    """Callback dari peraturan_files (OCR). Simpan ringkas ke state 'ocr'."""
    try:
        with _PROG_LOCK:
            _PROGRESS["fase"] = "ocr"
            oc = dict(_PROGRESS.get("ocr") or {})
            oc["event"] = event
            if isinstance(data, dict):
                for k in ("path", "pages", "i", "n", "chars"):
                    if k in data:
                        oc[k] = data[k]
            _PROGRESS["ocr"] = oc
    except Exception:
        pass


# ------------------------------------------------------------------- utilitas
def _iter_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for nama in filenames:
            if nama.startswith("."):
                continue
            if nama.lower() in SF.SKIP_NAMES:
                continue
            yield os.path.join(dirpath, nama)


def _didukung(path):
    ext = os.path.splitext(path.lower())[1]
    return ext in SF.DOC_EXT or ext in SF.IMG_EXT or ext in SF.LAWAS_EXT


def _source_id(root, path):
    try:
        rel = os.path.relpath(path, root)
    except Exception:
        rel = os.path.basename(path)
    rel = rel.replace("\\", "/")
    return rel


def _dokumen_id(source_id):
    base = re.sub(r"[^0-9A-Za-z]+", "-", source_id.lower()).strip("-")
    return "sop-" + (base or "dok")[:120]


def _tipe_hint(nama):
    ext = os.path.splitext(nama.lower())[1]
    if ext == ".pdf":
        return "pdf"
    if ext == ".pptx":
        return "pptx"
    if ext == ".docx":
        return "docx"
    if ext in (".html", ".htm"):
        return "html"
    if ext in (".txt", ".md"):
        return "teks"
    if ext in SF.IMG_EXT:
        return "gambar"
    if ext in SF.LAWAS_EXT:
        return "lawas"
    return "lain"


# ------------------------------------------------------------ penamaan hybrid
def _ai_judul_kategori(info):
    """Minta LLM usulkan {judul, kategori} dari cuplikan dokumen. Fallback ''."""
    if llm_client is None:
        return "", ""
    cuplik = (info.teks or "")[:1500]
    if not cuplik.strip():
        return "", ""
    sys = (
        "Anda pembantu pengarsipan dokumen SOP dan proses bisnis instansi pajak. "
        "Balas HANYA satu baris JSON valid tanpa penjelasan."
    )
    prompt = (
        "Dari cuplikan dokumen berikut, usulkan judul ringkas (maks 12 kata, Bahasa "
        "Indonesia, tanpa tanda kutip) dan kategori salah satu dari "
        "[\"SOP\", \"Proses Bisnis\", \"Panduan\", \"Lainnya\"].\n"
        "Nama berkas: %s\n"
        "Cuplikan:\n%s\n\n"
        'Format: {"judul": "...", "kategori": "..."}'
        % (os.path.basename(info.path), cuplik)
    )
    try:
        out = llm_client.chat([{"role": "user", "content": prompt}], system=sys,
                              max_new_tokens=120, temperature=0.2)
    except Exception:
        return "", ""
    judul, kategori = "", ""
    m = re.search(r"\{.*\}", out or "", re.S)
    if m:
        blob = m.group(0)
        jm = re.search(r'"judul"\s*:\s*"([^"]+)"', blob)
        km = re.search(r'"kategori"\s*:\s*"([^"]+)"', blob)
        if jm:
            judul = jm.group(1).strip()
        if km:
            kategori = km.group(1).strip()
    if kategori not in sdb.KATEGORI_VALID:
        kategori = ""
    return SF._bersih_judul(judul), kategori


def tentukan_nama(info, use_ai_naming):
    """Hybrid: pakai kandidat aturan; bila kurang bermakna & AI aktif -> LLM."""
    judul = info.judul_kandidat or ""
    kategori = info.kategori_kandidat or ""
    sumber = "aturan"
    perlu_ai = (not SF.judul_bermakna(judul)) or (not kategori)
    if perlu_ai and use_ai_naming:
        aj, ak = _ai_judul_kategori(info)
        if SF.judul_bermakna(aj):
            judul = aj
            sumber = "ai"
        if ak:
            kategori = ak
    if not SF.judul_bermakna(judul):
        judul = SF._nama_dari_file(info.path) or "Dokumen SOP"
    if not kategori:
        kategori = "Lainnya"
    return judul, kategori, sumber


# ------------------------------------------------------------------ ringkasan
def _ringkas_dokumen(judul, kategori, teks):
    """Ringkasan padat dokumen via LLM (setia pada isi). Fallback ''."""
    if llm_client is None:
        return ""
    cuplik = (teks or "").strip()
    if not cuplik:
        return ""
    cuplik = cuplik[:6000]
    sys = (
        "Anda asisten yang membuat ringkasan dokumen SOP dan proses bisnis "
        "layanan perpajakan DJP. Ringkas SETIA pada isi dokumen; DILARANG "
        "menambah fakta, angka, pasal, atau tautan yang tidak ada di teks."
    )
    prompt = (
        "Buat ringkasan padat Bahasa Indonesia (3-6 kalimat, bentuk paragraf, "
        "tanpa poin bernomor) dari dokumen berikut. Fokus pada: untuk siapa/kapan "
        "dokumen ini dipakai, langkah atau prosedur inti, serta syarat/dokumen "
        "penting bila disebut.\n"
        "Judul: %s\nKategori: %s\nIsi dokumen:\n%s"
        % (judul or "-", kategori or "-", cuplik)
    )
    try:
        out = llm_client.chat([{"role": "user", "content": prompt}], system=sys,
                              max_new_tokens=320, temperature=0.2)
    except Exception:
        return ""
    return SF._clean(out or "")[:1500]


# ------------------------------------------------------------------ potong isi
def _potong(bagian, isi, maks=SF.MAKS_ISI):
    isi = (isi or "").strip()
    if len(isi) <= maks:
        return [(bagian, isi)] if isi else []
    keping = []
    sisa = isi
    idx = 1
    while sisa:
        potong = sisa[:maks]
        # coba potong di batas baris/kalimat
        pos = max(potong.rfind("\n"), potong.rfind(". "))
        if pos > int(maks * 0.5):
            potong = sisa[:pos + 1]
        b = bagian if idx == 1 else ("%s (lanjutan %d)" % (bagian or "Bagian", idx))
        keping.append((b, potong.strip()))
        sisa = sisa[len(potong):].strip()
        idx += 1
        if idx > 50:
            break
    return keping


# ------------------------------------------------------------------- inti proses
def proses(root, do_ocr=False, ingest=True, use_ai_naming=True, conn=None,
           files=None, do_ringkas=True):
    """Jalankan impor (blocking). files=None telusuri folder; files=daftar path
    proses berkas itu saja (unggah). ingest=False -> pratinjau tanpa simpan."""
    own = conn is None
    conn = conn or sdb.init_db(sdb.connect())
    ringkas = {"total": 0, "ditambah": 0, "dilewati": 0, "gagal": 0,
               "perlu_ocr": 0, "unit": 0}
    log_rows = []
    try:
        PF.set_progress_cb(_on_files_progress)
    except Exception:
        pass
    try:
        if not root or not os.path.isdir(root):
            _prog(running=False, fase="error", error="Folder tidak ditemukan: %s" % root)
            print(_TAG + "folder tidak ada: %s" % root)
            return {"ok": False, "error": "Folder tidak ditemukan", "ringkas": ringkas}

        if files is not None:
            kandidat = [p for p in files if _didukung(p) and os.path.isfile(p)]
        else:
            kandidat = [p for p in _iter_files(root) if _didukung(p)]
        total = len(kandidat)
        _prog(running=True, fase="proses", root=root, total=total, selesai=0,
              ditambah=0, dilewati=0, gagal=0, perlu_ocr=0, error="", ocr={})
        print(_TAG + "mulai: %d berkas di %s (ocr=%s, ai=%s, ingest=%s, ringkas=%s)"
              % (total, root, do_ocr, use_ai_naming, ingest, do_ringkas))

        for i, path in enumerate(kandidat, start=1):
            nama_file = os.path.relpath(path, root)
            _prog(fase="proses", selesai=i - 1, file=nama_file, ocr={})
            tipe = _tipe_hint(path)
            try:
                info = SF.classify(path, do_ocr=do_ocr)
                if info.tipe in ("lawas", "unknown") or not info.sections:
                    status = "lewati"
                    catatan = info.catatan or "tak ada isi terekstrak"
                    if info.perlu_ocr:
                        status = "perlu_ocr"
                        ringkas["perlu_ocr"] += 1
                        _prog(perlu_ocr=ringkas["perlu_ocr"])
                    ringkas["dilewati"] += 1
                    _prog(dilewati=ringkas["dilewati"])
                    log_rows.append({"file": nama_file, "dokumen_id": "", "judul": "",
                                     "kategori": "", "tipe": tipe, "n_unit": 0,
                                     "status": status, "catatan": catatan})
                    continue

                judul, kategori, src_nama = tentukan_nama(info, use_ai_naming)
                sid = _source_id(root, path)
                did = _dokumen_id(sid)

                sec_units = []
                for sec in info.sections:
                    for (bag, isi) in _potong(sec.get("bagian", ""), sec.get("isi", "")):
                        if not isi:
                            continue
                        sec_units.append((bag, isi))

                if not sec_units:
                    ringkas["dilewati"] += 1
                    _prog(dilewati=ringkas["dilewati"])
                    log_rows.append({"file": nama_file, "dokumen_id": did, "judul": judul,
                                     "kategori": kategori, "tipe": tipe, "n_unit": 0,
                                     "status": "lewati", "catatan": "tak ada unit"})
                    continue

                if info.perlu_ocr:
                    ringkas["perlu_ocr"] += 1
                    _prog(perlu_ocr=ringkas["perlu_ocr"])

                ringkasan = ""
                if ingest and do_ringkas:
                    _prog(fase="ringkas", file=nama_file, judul=judul)
                    ringkasan = _ringkas_dokumen(judul, kategori, info.teks)
                    _prog(fase="proses")

                unit_defs = []
                urut = 0
                if ringkasan:
                    urut += 1
                    unit_defs.append((urut, "Ringkasan", ringkasan))
                for (bag, isi) in sec_units:
                    urut += 1
                    unit_defs.append((urut, bag, isi))

                if ingest:
                    sdb.delete_dokumen(did, conn=conn)  # segarkan bila impor ulang
                    for (urut, bag, isi) in unit_defs:
                        sdb.upsert_sop({
                            "id": "%s#%03d" % (did, urut),
                            "dokumen_id": did,
                            "judul": judul,
                            "kategori": kategori,
                            "bagian": bag,
                            "urutan": urut,
                            "isi": isi,
                            "ringkasan": ringkasan,
                            "sumber_tipe": tipe,
                            "status": "aktif",
                            "source_url": "",
                            "source_file": nama_file,
                            "source_id": sid,
                        }, conn=conn)

                ringkas["ditambah"] += 1
                ringkas["unit"] += len(unit_defs)
                _prog(ditambah=ringkas["ditambah"], judul=judul, n_unit=len(unit_defs))
                catatan = "judul via %s" % src_nama
                if ringkasan:
                    catatan += "; +ringkasan"
                if info.perlu_ocr:
                    catatan += "; perlu OCR"
                log_rows.append({"file": nama_file, "dokumen_id": did, "judul": judul,
                                 "kategori": kategori, "tipe": tipe,
                                 "n_unit": len(unit_defs), "status": "ada",
                                 "catatan": catatan})
            except Exception as e:
                ringkas["gagal"] += 1
                _prog(gagal=ringkas["gagal"])
                print(_TAG + "gagal %s: %s" % (nama_file, e))
                traceback.print_exc()
                log_rows.append({"file": nama_file, "dokumen_id": "", "judul": "",
                                 "kategori": "", "tipe": tipe, "n_unit": 0,
                                 "status": "gagal", "catatan": str(e)[:200]})
            finally:
                _prog(selesai=i)

        ringkas["total"] = total
        try:
            if log_rows:
                sdb.upsert_impor_log(log_rows, conn=conn)
        except Exception as e:
            print(_TAG + "gagal tulis impor_log: %s" % e)

        _prog(running=False, fase="selesai", file="",
              pesan="Selesai: +%d dok, %d unit, lewati %d, gagal %d, perlu OCR %d"
              % (ringkas["ditambah"], ringkas["unit"], ringkas["dilewati"],
                 ringkas["gagal"], ringkas["perlu_ocr"]))
        print(_TAG + get_progress().get("pesan", ""))
        return {"ok": True, "ringkas": ringkas}
    except Exception as e:
        _prog(running=False, fase="error", error=str(e)[:200])
        print(_TAG + "error fatal: %s" % e)
        traceback.print_exc()
        return {"ok": False, "error": str(e), "ringkas": ringkas}
    finally:
        try:
            PF.set_progress_cb(None)
        except Exception:
            pass
        if own:
            conn.close()


def proses_async(root, do_ocr=False, ingest=True, use_ai_naming=True,
                 files=None, do_ringkas=True):
    global _THREAD
    if is_running():
        return {"ok": False, "error": "Proses impor SOP masih berjalan."}
    reset_progress()
    _prog(running=True, fase="mulai", root=root)

    def _run():
        try:
            proses(root, do_ocr=do_ocr, ingest=ingest, use_ai_naming=use_ai_naming,
                   files=files, do_ringkas=do_ringkas)
        except Exception as e:
            _prog(running=False, fase="error", error=str(e)[:200])

    _THREAD = threading.Thread(target=_run, daemon=True)
    _THREAD.start()
    return {"ok": True, "started": True}


def proses_files_async(paths, root=None, do_ocr=False, use_ai_naming=True,
                       do_ringkas=True):
    """Proses daftar berkas (hasil unggah) memakai pipeline yang sama."""
    root = root or upload_dir()
    paths = list(paths or [])
    return proses_async(root, do_ocr=do_ocr, ingest=True, use_ai_naming=use_ai_naming,
                        files=paths, do_ringkas=do_ringkas)


# ---------------------------------------------------------------------- audit
def audit_folder(root, status_filter="", limit=5000, conn=None):
    """Rekonsiliasi: daftar berkas folder + status ada/belum di basis data.

    status baris: 'ada' (berkas sudah terindeks) | 'belum' (belum di DB) |
    'lawas' (format perlu konversi) | 'abaikan' (bukan format didukung).
    """
    own = conn is None
    conn = conn or sdb.init_db(sdb.connect())
    try:
        if not root or not os.path.isdir(root):
            return {"ok": False, "error": "Folder tidak ditemukan", "rows": []}
        terindeks = {}
        for s in sdb.list_sumber(conn=conn):
            sid = s.get("source_id") or ""
            terindeks[sid] = s
        rows = []
        ringkas = {"total": 0, "ada": 0, "belum": 0, "lawas": 0, "abaikan": 0}
        for path in _iter_files(root):
            nama = os.path.relpath(path, root)
            tipe = _tipe_hint(path)
            ext = os.path.splitext(path.lower())[1]
            sid = _source_id(root, path)
            if ext in SF.LAWAS_EXT:
                status = "lawas"
            elif ext in SF.DOC_EXT or ext in SF.IMG_EXT:
                status = "ada" if sid in terindeks else "belum"
            else:
                status = "abaikan"
            ringkas["total"] += 1
            ringkas[status] = ringkas.get(status, 0) + 1
            hit = terindeks.get(sid) or {}
            baris = {
                "file": nama,
                "tipe": tipe,
                "status": status,
                "judul": hit.get("judul", ""),
                "kategori": hit.get("kategori", ""),
                "dokumen_id": hit.get("dokumen_id", ""),
                "n_unit": hit.get("n_unit", 0),
                "source_id": sid,
            }
            if not status_filter or status_filter == status:
                rows.append(baris)
        rows.sort(key=lambda r: (r["status"] != "belum", r["kategori"], r["file"]))
        return {"ok": True, "ringkas": ringkas, "rows": rows[:limit],
                "ditampilkan": min(len(rows), limit), "limit": limit}
    finally:
        if own:
            conn.close()
