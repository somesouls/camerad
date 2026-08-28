# -*- coding: utf-8 -*-
"""rag_golden_db.py — Golden set evaluasi RAG (Fase 4).

Golden set = himpunan query TERKURASI dengan harapan hasil yang jelas, dipakai
sebagai gerbang kualitas sebelum upgrade model/perubahan retrieval masuk
produksi. Dua jenis harapan:

  * 'hit'     : retrieval HARUS menemukan rujukan tertentu di top-k.
                Ekspektasi dinyatakan fleksibel di expect_json:
                  {"nomor": ["PER-23/PJ/2025", ...],   # salah satu cocok
                   "keywords": ["kawasan berikat", ...]} # SEMUA muncul di satu baris
                (nomor dicocokkan setelah dinormalisasi — bebas tanda baca;
                keywords dicocokkan pada judul+hierarchy+isi huruf kecil.)
  * 'abstain' : topik SENGAJA tidak ada di basis data -> mesin seharusnya
                abstain / retrieval-nya lemah.

Dua cara pakai:
  1. Evaluasi retrieval deterministik TANPA LLM (recall@k, MRR, proksi abstain)
     lewat phase4_eval.py — murah, bisa jadi gerbang regresi CI/manual.
  2. Cermin ke eval_sample (jenis='golden') lewat mirror_to_eval() agar ikut
     dinilai LLM-judge di menu /rag-eval (keandalan + halusinasi + abstain).

Bonus: mine_feedback() menambang kandidat golden set dari log chat agent
(jempol-down / jawaban fallback) di agent_log_db.

DB file: env PIPELINE_GOLDEN_DB_FILE (default golden.db). Stdlib-only.
"""
import os
import json
import hashlib
import sqlite3
import datetime as _dt

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

JENIS_HARAPAN = ("hit", "abstain")


def default_db_path():
    return os.environ.get("PIPELINE_GOLDEN_DB_FILE") or os.path.join(_BASE_DIR, "golden.db")


def _now():
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def gid(query):
    return hashlib.sha1((query or "").strip().lower().encode("utf-8", "replace")).hexdigest()[:16]


def connect(db_path=None):
    conn = sqlite3.connect(db_path or default_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=8000;")
    return conn


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS rag_golden (
            id            TEXT PRIMARY KEY,
            query         TEXT,
            jenis_harapan TEXT,
            expect_json   TEXT,
            catatan       TEXT,
            aktif         INTEGER DEFAULT 1,
            created_at    TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_rg_harapan ON rag_golden(jenis_harapan, aktif);
        """
    )
    conn.commit()
    return conn


# ------------------------------------------------------------ seed bawaan
# Catatan: ekspektasi nomor dinormalisasi saat pencocokan, jadi variasi
# penulisan (PMK 28 TAHUN 2024 vs PMK No. 28 Tahun 2024) tetap cocok.
# v20: dua ekspektasi dilonggarkan berdasar temuan uji pertama (angka di pasal
# ditulis lengkap "183 (seratus delapan puluh tiga) hari"; istilah BKP tidak
# selalu dieja "barang kena pajak" pada unit yang sama).
# v21: ekspektasi PKP dilonggarkan ke akronim "pkp" — dokumen teratas hasil
# rerank untuk kueri PKP memakai akronim, sedangkan frasa penuh "pengusaha kena
# pajak" umumnya hanya muncul di pasal definisi UU yang terdemosi reranker
# (temuan Tahap 3 #2; retrieval sehat, ekspektasi yang terlalu literal).
_DEFAULT_SEED = [
    # ---- HIT: harus menemukan rujukan ----
    ("peraturan yang mengatur SPLN", "hit",
     {"nomor": ["PER-23/PJ/2025", "36 TAHUN 2008"],
      "keywords": ["subjek pajak luar negeri"],
      "gold": "PER-23/PJ/2025 / UU PPh (UU 36/2008) — definisi & kriteria SPLN"},
     "Query acuan audit."),
    ("apa itu subjek pajak luar negeri", "hit",
     {"nomor": ["PER-23/PJ/2025"], "keywords": ["subjek pajak luar negeri"],
      "gold": "Pasal definisi SPLN (PER-23/PJ/2025 Pasal 2/3)"},
     "Query definisi — domain boost definisi seharusnya mengangkat pasal definisi."),
    ("bedanya subjek pajak dalam negeri dan subjek pajak luar negeri", "hit",
     {"keywords": ["subjek pajak dalam negeri", "subjek pajak luar negeri"],
      "gold": "Ketentuan yang memuat kedua istilah sekaligus"},
     ""),
    ("kriteria orang pribadi menjadi subjek pajak dalam negeri", "hit",
     {"keywords": ["183", "bertempat tinggal"],
      "gold": "Kriteria SPDN orang pribadi (tinggal/183 hari/niat)"},
     "v20: keyword '183 hari' dilonggarkan jadi '183' (pasal menulis angka "
     "lengkap dalam teks)."),
    ("ketentuan penyerahan BKP dari luar daerah pabean ke kawasan berikat", "hit",
     {"keywords": ["kawasan berikat"],
      "gold": "Ketentuan PPN penyerahan TLDDP -> kawasan berikat"},
     "Query acuan audit (ejaan informal: pabedan -> pabean). v20: keyword "
     "dilonggarkan (BKP tak selalu dieja penuh pada unit yang sama)."),
    ("fasilitas PPN penyerahan ke kawasan berikat", "hit",
     {"keywords": ["kawasan berikat", "pajak pertambahan nilai"],
      "gold": "Fasilitas PPN kawasan berikat"},
     ""),
    ("apa itu barang kena pajak", "hit",
     {"keywords": ["barang kena pajak"], "gold": "Definisi BKP"}, ""),
    ("apa yang dimaksud jasa kena pajak", "hit",
     {"keywords": ["jasa kena pajak"], "gold": "Definisi JKP"}, ""),
    ("fasilitas pengurangan pajak penghasilan badan di ibu kota nusantara", "hit",
     {"nomor": ["PMK 28 TAHUN 2024", "28 TAHUN 2024"],
      "keywords": ["ibu kota nusantara"],
      "gold": "PMK 28/2024 — fasilitas PPh badan IKN"},
     "Dokumen teramati ada di basis data."),
    ("bunyi pasal 19 PER-23/PJ/2016", "hit",
     {"nomor": ["PER-23/PJ/2016"], "gold": "PER-23/PJ/2016 Pasal 19"},
     "Dokumen teramati ada di basis data. Query bernomor exact — menguji FTS v3 "
     "(kolom nomor)."),
    ("ketentuan peralihan PER-8/PJ/2025", "hit",
     {"nomor": ["PER-8/PJ/2025", "8/PJ/2025"],
      "gold": "PER-8/PJ/2025 — ketentuan peralihan"},
     "Dokumen teramati ada di basis data. Query bernomor exact — menguji FTS v3."),
    ("pengertian pengusaha kena pajak", "hit",
     {"keywords": ["pkp"],
      "gold": "Definisi/pengukuhan PKP — dokumen relevan lazim memakai akronim 'PKP'"},
     "v21: keyword dilonggarkan 'pengusaha kena pajak' -> 'pkp' (dokumen teratas "
     "hasil rerank memakai akronim; frasa penuh umumnya hanya di pasal definisi UU)."),

    # ---- HIT: pasangan formal<->kolokial (Tahap 3 #2) ----
    # Twin gaya-santai dari kueri formal di atas; expect SAMA persis agar
    # apel-ke-apel. MISS di twin = celah robustness gaya bahasa/akronim
    # (rekomendasi laporan), BUKAN celah cakupan data — dokumen dijamin ada
    # karena ini kembaran kueri yang sudah terbukti HIT.
    ("pkp itu apa sih", "hit",
     {"keywords": ["pkp"],
      "gold": "Definisi PKP (twin kolokial) — terima akronim 'PKP'"},
     "Pasangan kolokial (Tahap 3 #2). v21: keyword dilonggarkan -> 'pkp'."),
    ("bkp tuh apa ya", "hit",
     {"keywords": ["barang kena pajak"],
      "gold": "Definisi BKP — twin kolokial 'apa itu barang kena pajak'"},
     "Pasangan kolokial (Tahap 3 #2): akronim + gaya santai."),
    ("jkp maksudnya gimana", "hit",
     {"keywords": ["jasa kena pajak"],
      "gold": "Definisi JKP — twin kolokial 'apa yang dimaksud jasa kena pajak'"},
     "Pasangan kolokial (Tahap 3 #2): akronim + gaya santai."),
    ("bedanya spdn sama spln apa", "hit",
     {"keywords": ["subjek pajak dalam negeri", "subjek pajak luar negeri"],
      "gold": "SPDN vs SPLN — twin kolokial akronim"},
     "Pasangan kolokial (Tahap 3 #2): akronim SPDN/SPLN."),
    ("keringanan pajak buat perusahaan di ikn", "hit",
     {"nomor": ["PMK 28 TAHUN 2024", "28 TAHUN 2024"],
      "keywords": ["ibu kota nusantara"],
      "gold": "PMK 28/2024 fasilitas PPh badan IKN — twin kolokial"},
     "Pasangan kolokial (Tahap 3 #2): 'keringanan pajak' <-> 'fasilitas pengurangan PPh badan'."),
    ("kirim barang dari luar negeri ke kawasan berikat gimana aturannya", "hit",
     {"keywords": ["kawasan berikat"],
      "gold": "PPN penyerahan ke kawasan berikat — twin kolokial"},
     "Pasangan kolokial (Tahap 3 #2): frasa awam impor/TLDDP."),

    # ---- ABSTAIN: topik sengaja di luar basis data ----
    ("cara mengajukan SPLN di coretax", "abstain",
     {"gold": "Bukan prosedur yang ada (SPLN = status, bukan aplikasi)"},
     "Teramati abstain pada uji live 19 Agu 2026."),
    ("jadwal konser dewa 19 tahun 2026", "abstain", {"gold": "Di luar domain"}, ""),
    ("resep nasi goreng spesial", "abstain", {"gold": "Di luar domain"}, ""),
    ("cara memperbaiki printer mati total", "abstain", {"gold": "Di luar domain"}, ""),
    ("harga bitcoin hari ini", "abstain", {"gold": "Di luar domain"}, ""),
    ("lowongan kerja di DJP", "abstain", {"gold": "Di luar domain"}, ""),
    ("cara instal windows 11", "abstain", {"gold": "Di luar domain"}, ""),
    ("siapa presiden indonesia tahun 2040", "abstain", {"gold": "Di luar domain"}, ""),
]

# v20: perbaikan ekspektasi untuk entri yang SUDAH ter-seed versi lama (v19).
# Hanya menyentuh entri yang expect-nya MASIH versi lama — suntingan admin
# tidak pernah ditimpa.
# v21: dua entri PKP ditambahkan (formal + twin kolokial) — longgarkan ke 'pkp'.
_SEED_V2_FIX = (
    ("kriteria orang pribadi menjadi subjek pajak dalam negeri",
     ["183 hari", "bertempat tinggal"],
     {"nomor": [], "keywords": ["183", "bertempat tinggal"],
      "gold": "Kriteria SPDN orang pribadi (tinggal/183 hari/niat)"}),
    ("ketentuan penyerahan BKP dari luar daerah pabean ke kawasan berikat",
     ["kawasan berikat", "barang kena pajak"],
     {"nomor": [], "keywords": ["kawasan berikat"],
      "gold": "Ketentuan PPN penyerahan TLDDP -> kawasan berikat"}),
    ("pengertian pengusaha kena pajak",
     ["pengusaha kena pajak"],
     {"nomor": [], "keywords": ["pkp"],
      "gold": "Definisi/pengukuhan PKP — dokumen relevan lazim memakai akronim 'PKP'"}),
    ("pkp itu apa sih",
     ["pengusaha kena pajak"],
     {"nomor": [], "keywords": ["pkp"],
      "gold": "Definisi PKP (twin kolokial) — terima akronim 'PKP'"}),
)


# --------------------------------------------------------------------- CRUD
def upsert_golden(query, jenis_harapan="hit", expect=None, catatan="",
                  aktif=1, conn=None):
    jh = (jenis_harapan or "hit").strip().lower()
    if jh not in JENIS_HARAPAN:
        raise ValueError("jenis_harapan harus salah satu dari %s" % (JENIS_HARAPAN,))
    own = conn is None
    conn = conn or init_db(connect())
    try:
        conn.execute(
            "INSERT INTO rag_golden(id, query, jenis_harapan, expect_json, catatan, aktif, created_at) "
            "VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET jenis_harapan=excluded.jenis_harapan, "
            "expect_json=excluded.expect_json, catatan=excluded.catatan, aktif=excluded.aktif",
            (gid(query), (query or "").strip(), jh,
             json.dumps(expect or {}, ensure_ascii=False), catatan or "",
             int(bool(aktif)), _now()),
        )
        conn.commit()
        return {"id": gid(query)}
    finally:
        if own:
            conn.close()


def list_golden(conn=None, only_aktif=False, jenis_harapan=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        q = "SELECT * FROM rag_golden"
        where, params = [], []
        if only_aktif:
            where.append("aktif=1")
        if jenis_harapan:
            where.append("jenis_harapan=?")
            params.append(jenis_harapan)
        if where:
            q += " WHERE " + " AND ".join(where)
        q += " ORDER BY jenis_harapan, id"
        out = []
        for r in conn.execute(q, params).fetchall():
            d = dict(r)
            try:
                d["expect"] = json.loads(d.pop("expect_json") or "{}")
            except Exception:
                d["expect"] = {}
            d["aktif"] = bool(d.get("aktif"))
            out.append(d)
        return out
    finally:
        if own:
            conn.close()


def set_aktif(golden_id, aktif, conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        conn.execute("UPDATE rag_golden SET aktif=? WHERE id=?",
                     (int(bool(aktif)), golden_id))
        conn.commit()
        return {"ok": True}
    finally:
        if own:
            conn.close()


def seed_default(conn=None):
    """Mode MERGE idempoten: menambah entri bawaan yang belum ada; tidak
    menimpa entri yang sudah disunting admin."""
    own = conn is None
    conn = conn or init_db(connect())
    try:
        n = 0
        for q, jh, expect, catatan in _DEFAULT_SEED:
            cur = conn.execute(
                "INSERT OR IGNORE INTO rag_golden(id, query, jenis_harapan, expect_json, catatan, aktif, created_at) "
                "VALUES(?,?,?,?,?,1,?)",
                (gid(q), q, jh, json.dumps(expect, ensure_ascii=False), catatan, _now()),
            )
            n += cur.rowcount or 0
        conn.commit()
        if n:
            print("[rag_golden_db] seed merge: +%d entri bawaan" % n, flush=True)
        return n
    finally:
        if own:
            conn.close()


def fix_seed_v2(conn=None):
    """v20/v21: longgarkan ekspektasi yang terbukti terlalu ketat pada uji
    (lihat _SEED_V2_FIX). Hanya menyentuh entri yang expect-nya MASIH versi
    lama; suntingan admin tidak pernah ditimpa. Kembalikan {'updated': n}."""
    own = conn is None
    conn = conn or init_db(connect())
    n = 0
    try:
        for q, kw_lama, expect_baru in _SEED_V2_FIX:
            r = conn.execute("SELECT expect_json FROM rag_golden WHERE id=?",
                             (gid(q),)).fetchone()
            if not r:
                continue
            try:
                ex = json.loads(r["expect_json"] or "{}")
            except Exception:
                continue
            if ex.get("keywords") != kw_lama:
                continue  # sudah versi baru / disunting admin
            conn.execute(
                "UPDATE rag_golden SET expect_json=? WHERE id=?",
                (json.dumps(expect_baru, ensure_ascii=False), gid(q)))
            n += 1
        conn.commit()
        if n:
            print("[rag_golden_db] fix seed v2: %d ekspektasi dilonggarkan" % n,
                  flush=True)
        return {"updated": n}
    finally:
        if own:
            conn.close()


# ------------------------------------------------ cermin ke harness /rag-eval
def _gold_text(entry):
    """Deskripsi harapan untuk kolom gold di eval_sample."""
    ex = entry.get("expect") or {}
    if entry.get("jenis_harapan") == "abstain":
        base = "HARAPAN: mesin ABSTAIN (topik sengaja di luar basis data)."
    else:
        bit = []
        if ex.get("nomor"):
            bit.append("nomor salah satu dari: " + ", ".join(ex["nomor"]))
        if ex.get("keywords"):
            bit.append("memuat kata kunci: " + ", ".join(ex["keywords"]))
        base = "HARAPAN HIT — " + ("; ".join(bit) if bit else "rujukan relevan")
    if ex.get("gold"):
        base += " | " + str(ex["gold"])
    return base


def mirror_to_eval(conn=None):
    """Cerminkan entri golden AKTIF ke eval_db.eval_sample (jenis='golden')
    sehingga ikut dinilai LLM-judge di menu /rag-eval (run dengan jenis=golden).
    Kembalikan jumlah entri yang dicerminkan."""
    try:
        import evaluation.db as eval_db
    except Exception as e:
        return {"ok": False, "error": "eval_db tak tersedia: %s" % e, "n": 0}
    n = 0
    ec = None
    try:
        ec = eval_db.init_db(eval_db.connect())
        for g in list_golden(only_aktif=True):
            eval_db.upsert_sample(
                ec, "golden", g["query"], gold=_gold_text(g), label="golden",
                sumber_ref="golden", meta={"golden_id": g["id"],
                                           "jenis_harapan": g["jenis_harapan"]},
                holdout=0)
            n += 1
        return {"ok": True, "n": n}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200], "n": n}
    finally:
        if ec is not None:
            try:
                ec.close()
            except Exception:
                pass


# ---------------------------------------------- penambangan feedback produksi
def mine_feedback(limit=400):
    """Tambang kandidat golden set dari log chat produksi (agent_log_db):
    pertanyaan dengan jempol DOWN atau jawaban tak-grounded (fallback).
    Kembalikan daftar {question, n_down, n_fallback, n_total, last_ts} urut
    prioritas (paling bermasalah dulu)."""
    try:
        import db.agent_log_db as alog
    except Exception as e:
        return {"ok": False, "error": "agent_log_db tak tersedia: %s" % e, "items": []}
    agg = {}

    def _serap(res, kunci):
        for row in (res or {}).get("logs") or []:
            q = (row.get("question") or "").strip()
            if not q:
                continue
            k = q.lower()
            a = agg.setdefault(k, {"question": q, "n_down": 0, "n_fallback": 0,
                                   "n_total": 0, "last_ts": ""})
            a[kunci] += 1
            a["n_total"] += 1
            ts = str(row.get("ts") or "")
            if ts > a["last_ts"]:
                a["last_ts"] = ts

    try:
        _serap(alog.list_logs(feedback="down", profil="", limit=limit), "n_down")
        _serap(alog.list_logs(grounded="0", profil="", limit=limit), "n_fallback")
    except Exception as e:
        return {"ok": False, "error": str(e)[:200], "items": []}
    items = sorted(agg.values(),
                   key=lambda a: (-(a["n_down"] * 3 + a["n_fallback"]), -a["n_total"]))
    return {"ok": True, "items": items, "n": len(items)}
