# -*- coding: utf-8 -*-
"""
peraturan_db.py
---------------
Basis data PERATURAN perpajakan (sumber resource #5) untuk camerad.

Diadaptasi dari repositori jakai (app/db.py, app/repo.py, app/retrieval.py,
app/index.py). Perbedaan utama vs jakai:
  * TANPA binary extension sqlite-vec. Vektor e5 disimpan sebagai BLOB pada
    tabel peraturan_vec dan cosine dihitung di Python (numpy) -> jalan mulus di
    lingkungan camerad.
  * Retrieval hybrid = FTS5 (lexical) + vektor e5 (semantik), digabung dengan
    Reciprocal Rank Fusion (RRF), sama seperti jakai.
  * Gagal-anggun: bila FTS5 tak tersedia -> LIKE; bila embedding tak tersedia ->
    FTS/LIKE saja.

Fase 1 — FTS v2 (ternormalisasi + bm25 berbobot):
  * Tabel meta `peraturan_meta` menyimpan penanda versi indeks.
  * `rebuild_fts_norm()` membangun ulang indeks dengan konten TERNORMALISASI via
    text_norm (lowercase + buang diakritik + buang stopword + stemming Sastrawi
    bila ada). Setelah migrasi, query token ternormalisasi + bm25 BERBOBOT.

v20 — FTS v3 (kolom NOMOR ikut terindeks):
  * Temuan golden set Fase 4: query bernomor exact ("bunyi pasal 19
    PER-23/PJ/2016") tidak bisa cocok leksikal karena kolom `nomor` tidak masuk
    indeks FTS. v3 menambah kolom `nomor` (jenis+nomor, normalisasi ringan —
    identifier tanpa stopword/stemming) dengan bobot bm25 TERTINGGI (12x;
    judul 8x, hierarchy 4x, isi 1x).
  * Versi indeks dibaca dari peraturan_meta; _sync_fts menulis sesuai versi
    tabel yang terpasang (v1/v2/v3) — transisi aman, tanpa migrasi paksa.
  * Target versi = FTS_TARGET_VERSION (dipakai phase1_upgrade).

Fase 2 — legal intelligence:
  * Kolom baru peraturan_unit: `topik`, `entitas` (JSON), `jenis_unit`
    (batang_tubuh/penjelasan/lampiran) — migrasi lunak via ALTER TABLE.
  * Tabel `peraturan_relasi` (from_source -> to_source, jenis penerus/pendahulu)
    dibangun oleh build_relasi() dari kolom status_terkait/history_terkait.
  * trace_successor(): penelusuran rantai penerus MULTI-HOP sampai dokumen
    berstatus 'berlaku' (dipakai rag_successor_patch v2).
  * tag_unit()/backfill_tags(): pengisian entitas/topik dictionary-driven
    (kamus_sinonim + taksonomi topik bawaan); auto-tag juga berjalan saat
    upsert_peraturan.

Kolom relasi status (diisi parser dari HTML TKB):
  * status_terkait  : JSON daftar peraturan TERBARU yang mengubah/mencabut
    (dari kotak legenda_status), urut dari atas. Tiap item memuat tanggal,
    nomor, judul, deskripsi, source_id (ID peraturan), href, dan link absolut.
  * history_terkait : JSON daftar peraturan SEBELUMNYA (dari legenda_history).

Konkurensi (mencegah 'database is locked'):
  SQLite hanya mengizinkan SATU penulis pada satu waktu. Proses batch menahan
  transaksi tulis selama meng-embed tiap baris (e5 di CPU bisa lambat), jadi
  koneksi lain yang menulis bisa menunggu. Karena itu koneksi memakai WAL
  (pembaca tak memblok penulis), busy_timeout 30 dtk, dan synchronous=NORMAL
  agar commit lebih ringan sehingga jendela kunci lebih pendek. Hindari juga
  menjalankan dua tugas tulis berat sekaligus (mis. batch + reindex bersamaan).

Pola koneksi mengikuti modul *_db.py camerad lain (sqlite3 + WAL).
"""
import os
import re
import json
import sqlite3

import peraturan.semantic as psem

try:
    import common.text_norm as tnorm
except Exception:            # pragma: no cover
    tnorm = None

try:
    import numpy as np
except Exception:
    np = None

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RRF_K = 60

# Detik menunggu bila DB sedang dikunci penulis lain sebelum menyerah.
_BUSY_TIMEOUT_MS = 30000

PERATURAN_KOLOM = [
    "id", "jenis_peraturan", "nomor", "tahun", "judul", "bab", "bagian",
    "paragraf", "pasal", "ayat", "huruf", "angka", "lampiran", "isi",
    "hierarchy", "reference", "status", "valid_from", "valid_to",
    "dicabut_oleh", "diubah_oleh", "jenis_perubahan", "target_pasal",
    "status_terkait", "history_terkait",
    "kekuatan_hukum", "can_cite", "source_url", "source_file", "source_id",
    "topik", "entitas", "jenis_unit",
]

_INT_FIELDS = ("tahun", "kekuatan_hukum", "can_cite")

# Kolom tambahan (untuk migrasi DB lama lewat ALTER TABLE ADD COLUMN).
_KOLOM_TAMBAHAN = (
    ("status_terkait", "TEXT"),
    ("history_terkait", "TEXT"),
    ("topik", "TEXT"),
    ("entitas", "TEXT"),
    ("jenis_unit", "TEXT"),
)

IMPOR_KOLOM = [
    "file", "source_id", "kategori", "tipe", "jenis", "nomor",
    "n_unit", "status", "catatan",
]


def floor_skor():
    try:
        return float(os.environ.get("PERATURAN_FLOOR_SKOR", "0.010"))
    except Exception:
        return 0.010


def default_db_path():
    return os.environ.get("PIPELINE_PERATURAN_DB_FILE") or os.path.join(_BASE_DIR, "peraturan.db")


def connect(db_path=None):
    # timeout= memberi lapisan tunggu di sisi driver; busy_timeout menjaga di
    # sisi engine SQLite. Keduanya diset agar penulis bersabar saat DB terkunci
    # penulis lain (mis. saat batch berjalan) alih-alih langsung 'database is locked'.
    conn = sqlite3.connect(db_path or default_db_path(), timeout=_BUSY_TIMEOUT_MS / 1000.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=%d;" % _BUSY_TIMEOUT_MS)
    return conn


_HAS_FTS = None


def _fts_available(conn):
    global _HAS_FTS
    if _HAS_FTS is not None:
        return _HAS_FTS
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts_probe USING fts5(x)")
        conn.execute("DROP TABLE IF EXISTS _fts_probe")
        _HAS_FTS = True
    except Exception:
        _HAS_FTS = False
    return _HAS_FTS


def _migrasi_kolom(conn):
    """Tambah kolom baru pada peraturan_unit bila DB dibuat versi lama."""
    try:
        ada = {r[1] for r in conn.execute("PRAGMA table_info(peraturan_unit)").fetchall()}
    except Exception:
        return
    for nama, tipe in _KOLOM_TAMBAHAN:
        if nama not in ada:
            try:
                conn.execute("ALTER TABLE peraturan_unit ADD COLUMN %s %s" % (nama, tipe))
            except Exception:
                pass


# Cache 'sudah init' per path DB supaya tiap connect() dari request UI tidak
# menjalankan ulang DDL (executescript) yang mengambil write-lock tak perlu ->
# ikut mengurangi kemungkinan 'database is locked' saat batch berjalan.
_INIT_DONE = set()


def init_db(conn, force=False):
    key = None
    try:
        r = conn.execute("PRAGMA database_list").fetchone()
        key = r[2] if r else None
    except Exception:
        key = None
    if not force and key and key in _INIT_DONE:
        return conn
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS peraturan_unit (
            id              TEXT PRIMARY KEY,
            jenis_peraturan TEXT,
            nomor           TEXT,
            tahun           INTEGER,
            judul           TEXT,
            bab             TEXT,
            bagian          TEXT,
            paragraf        TEXT,
            pasal           TEXT,
            ayat            TEXT,
            huruf           TEXT,
            angka           TEXT,
            lampiran        TEXT,
            isi             TEXT,
            hierarchy       TEXT,
            reference       TEXT,
            status          TEXT DEFAULT 'berlaku',
            valid_from      TEXT,
            valid_to        TEXT,
            dicabut_oleh    TEXT,
            diubah_oleh     TEXT,
            jenis_perubahan TEXT,
            target_pasal    TEXT,
            status_terkait  TEXT,
            history_terkait TEXT,
            kekuatan_hukum  INTEGER,
            can_cite        INTEGER DEFAULT 1,
            source_url      TEXT,
            source_file     TEXT,
            source_id       TEXT,
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_per_nomor ON peraturan_unit(jenis_peraturan, nomor);
        CREATE INDEX IF NOT EXISTS idx_per_status ON peraturan_unit(status);
        CREATE INDEX IF NOT EXISTS idx_per_source ON peraturan_unit(source_id);

        CREATE TABLE IF NOT EXISTS peraturan_vec (
            id  TEXT PRIMARY KEY,
            dim INTEGER,
            emb BLOB
        );

        CREATE TABLE IF NOT EXISTS impor_log (
            file       TEXT PRIMARY KEY,
            source_id  TEXT,
            kategori   TEXT,
            tipe       TEXT,
            jenis      TEXT,
            nomor      TEXT,
            n_unit     INTEGER DEFAULT 0,
            status     TEXT,
            catatan    TEXT,
            ts         TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS peraturan_meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS peraturan_relasi (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            from_source   TEXT,
            to_source     TEXT,
            jenis_relasi  TEXT,
            tanggal       TEXT,
            nomor_tujuan  TEXT,
            judul_tujuan  TEXT,
            link          TEXT,
            deskripsi     TEXT,
            UNIQUE(from_source, to_source, jenis_relasi)
        );
        CREATE INDEX IF NOT EXISTS idx_relasi_from ON peraturan_relasi(from_source, jenis_relasi);
        CREATE INDEX IF NOT EXISTS idx_relasi_to ON peraturan_relasi(to_source, jenis_relasi);
        """
    )
    _migrasi_kolom(conn)
    if _fts_available(conn):
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS peraturan_fts "
                "USING fts5(id UNINDEXED, judul, isi, "
                "tokenize='unicode61 remove_diacritics 2')"
            )
        except Exception:
            pass
    conn.commit()
    if key:
        _INIT_DONE.add(key)
    return conn


# --------------------------------------------------------------- cache vektor
_VEC_CACHE = {"sig": None, "ids": None, "mat": None}


def _vec_cache_clear():
    _VEC_CACHE["sig"] = None
    _VEC_CACHE["ids"] = None
    _VEC_CACHE["mat"] = None


def _vec_sig(conn):
    try:
        r = conn.execute("SELECT COUNT(*), COALESCE(MAX(rowid),0) FROM peraturan_vec").fetchone()
        return (int(r[0]), int(r[1]))
    except Exception:
        return (0, 0)


def _load_vectors(conn):
    if np is None:
        return [], None
    sig = _vec_sig(conn)
    if _VEC_CACHE["sig"] == sig and _VEC_CACHE["mat"] is not None:
        return _VEC_CACHE["ids"], _VEC_CACHE["mat"]
    try:
        rows = conn.execute("SELECT id, emb FROM peraturan_vec").fetchall()
    except Exception:
        return [], None
    ids, vecs = [], []
    for r in rows:
        v = psem.from_blob(r["emb"])
        if v is None:
            continue
        ids.append(r["id"])
        vecs.append(v)
    mat = np.vstack(vecs) if vecs else None
    _VEC_CACHE["sig"] = sig
    _VEC_CACHE["ids"] = ids
    _VEC_CACHE["mat"] = mat
    return ids, mat


# --------------------------------------------------------------- FTS berversi
# Versi indeks yang dibentuk rebuild_fts_norm. v3 (v20) menambah kolom `nomor`
# (bobot bm25 tertinggi) agar query bernomor exact cocok secara leksikal.
FTS_TARGET_VERSION = "3"

def _norm_text(t):
    """Teks ternormalisasi via text_norm; fallback lowercase bila modul absen."""
    if tnorm is not None:
        try:
            return tnorm.normalize(t)
        except Exception:
            pass
    return (t or "").lower()


_FTS_VER_CACHE = {"v": None}


def _fts_ver(conn):
    """Versi indeks FTS terpasang: '1' legacy (id,judul,isi) / '2' ternormalisasi
    +hierarchy / '3' +kolom nomor. Dibaca dari peraturan_meta."""
    if _FTS_VER_CACHE["v"] is not None:
        return _FTS_VER_CACHE["v"]
    v = "1"
    try:
        r = conn.execute(
            "SELECT value FROM peraturan_meta WHERE key='fts_version'").fetchone()
        if r and str(r[0]) in ("2", "3"):
            v = str(r[0])
    except Exception:
        v = "1"
    _FTS_VER_CACHE["v"] = v
    return v


def fts_v2_refresh():
    """Bersihkan cache versi FTS (dipanggil rebuild_fts_norm setelah migrasi)."""
    _FTS_VER_CACHE["v"] = None


def fts_info(conn=None):
    """Diagnosis versi & cakupan indeks FTS (dipakai phase1_upgrade)."""
    own = conn is None
    conn = conn or init_db(connect())
    try:
        ver = _fts_ver(conn)
        try:
            n = conn.execute("SELECT COUNT(*) FROM peraturan_fts").fetchone()[0]
        except Exception:
            n = 0
        info = {"fts_version": ver, "target": FTS_TARGET_VERSION,
                "fts_rows": int(n or 0), "norm_modul": tnorm is not None}
        if tnorm is not None:
            try:
                info["stemming"] = bool(tnorm.info().get("sastrawi"))
            except Exception:
                info["stemming"] = False
        return info
    finally:
        if own:
            conn.close()


def rebuild_fts_norm(conn=None, batch=500, progress=True):
    """Bangun ulang peraturan_fts menjadi v%s: kolom (id, nomor, judul,
    hierarchy, isi) — judul/hierarchy/isi TERNORMALISASI (text_norm), sedangkan
    nomor (jenis+nomor) memakai normalisasi ringan (identifier: tanpa stopword/
    stemming) agar token nomor cocok persis. Tandai meta fts_version.

    Idempoten; aman dijalankan kapan saja; TIDAK butuh model embedding/GPU.
    SEBAIKNYA tidak dijalankan bersamaan dengan reindex embedding (menulis DB
    yang sama).""" % FTS_TARGET_VERSION
    own = conn is None
    conn = conn or init_db(connect())
    try:
        if not _fts_available(conn):
            return {"ok": False, "error": "FTS5 tidak tersedia.", "n": 0}
        conn.execute("DROP TABLE IF EXISTS peraturan_fts")
        conn.execute(
            "CREATE VIRTUAL TABLE peraturan_fts USING fts5("
            "id UNINDEXED, nomor, judul, hierarchy, isi, "
            "tokenize='unicode61 remove_diacritics 2')")
        rows = conn.execute(
            "SELECT id, jenis_peraturan, nomor, judul, hierarchy, isi "
            "FROM peraturan_unit").fetchall()
        n = 0
        for i in range(0, len(rows), batch):
            for r in rows[i:i + batch]:
                nomor_txt = _light_norm(
                    "%s %s" % (r["jenis_peraturan"] or "", r["nomor"] or ""))
                conn.execute(
                    "INSERT INTO peraturan_fts(id, nomor, judul, hierarchy, isi) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (r["id"], nomor_txt, _norm_text(r["judul"]),
                     _norm_text(r["hierarchy"]), _norm_text(r["isi"])),
                )
                n += 1
            conn.commit()
            if progress:
                print("[peraturan_db] rebuild FTS v%s: %d/%d"
                      % (FTS_TARGET_VERSION, n, len(rows)), flush=True)
        conn.execute(
            "INSERT INTO peraturan_meta(key, value) VALUES('fts_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (FTS_TARGET_VERSION,))
        conn.commit()
        fts_v2_refresh()
        return {"ok": True, "n": n, "fts_version": FTS_TARGET_VERSION}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200], "n": 0}
    finally:
        if own:
            conn.close()


# --------------------------------------------------------------- Fase 2: tagging
# Tagger ringan (TANPA stemming — cukup lowercase + rapikan non-alfanumerik)
# agar cocok dipakai saat upsert batch besar maupun backfill.
def _light_norm(t):
    return re.sub(r"[^a-z0-9]+", " ", str(t or "").lower())


# Taksonomi topik bawaan (keyword pada teks ternormalisasi ringan). Admin bisa
# memperkaya ENTITAS lewat menu Kamus (kamus_sinonim) — tagger otomatis ikut.
_TOPIK_RULES = [
    ("PPN", ["pajak pertambahan nilai", "ppn", "ppnbm", "barang kena pajak",
             "jasa kena pajak", "faktur pajak", "kawasan berikat",
             "daerah pabean", "pengusaha kena pajak"]),
    ("PPh", ["pajak penghasilan", "pph pasal", "subjek pajak",
             "penghasilan kena pajak", "penghasilan tidak kena pajak",
             "bentuk usaha tetap", "p3b", "penghindaran pajak berganda"]),
    ("KUP", ["ketentuan umum dan tata cara perpajakan", "surat pemberitahuan",
             "ketetapan pajak", "keberatan", "banding", "gugatan",
             "nomor pokok wajib pajak", "npwp"]),
    ("Kepabeanan", ["pabean", "cukai", "tempat penimbunan berikat",
                    "bea masuk", "pemberitahuan impor barang",
                    "pemberitahuan ekspor barang"]),
    ("PBB_BPHTB", ["pajak bumi dan bangunan", "pbb",
                   "perolehan hak atas tanah", "bphtb"]),
    ("Bea Meterai", ["bea meterai", "meterai"]),
    ("Sanksi & Penagihan", ["sanksi administrasi", "denda", "bunga",
                            "surat paksa", "penagihan", "juru sita"]),
    ("Insentif & Fasilitas", ["insentif", "fasilitas", "dibebaskan",
                              "tidak dipungut", "pengurangan", "tax holiday"]),
]

_TAGGER = {"built": False, "rx": None, "form2istilah": {}}


def _build_tagger():
    """Bangun peta bentuk->istilah dari kamus_sinonim + regex gabungan (lazy,
    fail-soft). Bentuk diambil mentah dari kamus lalu di-light-normalize."""
    if _TAGGER["built"]:
        return
    _TAGGER["built"] = True
    forms = {}
    try:
        import rag.kamus_db as kdb
        for e in (kdb.all_active() or []):
            ist = str(e.get("istilah") or "").strip()
            if not ist:
                continue
            for f in (e.get("forms") or []):
                fl = _light_norm(f).strip()
                if len(fl) >= 2:
                    forms.setdefault(fl, ist)
    except Exception:
        forms = {}
    _TAGGER["form2istilah"] = forms
    if forms:
        try:
            pat = "|".join(re.escape(f) for f in sorted(forms, key=len, reverse=True))
            _TAGGER["rx"] = re.compile(r"(?<![0-9a-z])(" + pat + r")(?![0-9a-z])")
        except Exception:
            _TAGGER["rx"] = None


def tag_unit(judul, hierarchy, isi):
    """Kembalikan (entitas_json, topik_json) untuk satu unit.

    entitas: daftar istilah kamus yang bentuknya muncul di teks.
    topik  : daftar topik taksonomi bawaan yang keyword-nya muncul.
    """
    _build_tagger()
    txt = _light_norm(" ".join(x for x in [judul, hierarchy, isi] if x))
    ent = []
    rx = _TAGGER["rx"]
    if rx and txt:
        try:
            for m in rx.finditer(txt):
                ist = _TAGGER["form2istilah"].get(m.group(1))
                if ist and ist not in ent:
                    ent.append(ist)
        except Exception:
            pass
    topik = []
    for nama, kws in _TOPIK_RULES:
        for kw in kws:
            if kw in txt:
                topik.append(nama)
                break
    return (json.dumps(ent, ensure_ascii=False) if ent else None,
            json.dumps(topik, ensure_ascii=False) if topik else None)


def backfill_tags(conn=None, batch=500, progress=True):
    """Fase 2: isi kolom entitas & topik untuk seluruh unit (idempoten).
    Tanpa model/GPU; satu regex gabungan per unit."""
    own = conn is None
    conn = conn or init_db(connect())
    try:
        rows = conn.execute(
            "SELECT id, judul, hierarchy, isi FROM peraturan_unit").fetchall()
    except Exception as e:
        if own:
            conn.close()
        return {"ok": False, "error": str(e)[:200], "n": 0}
    n = 0
    try:
        for i in range(0, len(rows), batch):
            for r in rows[i:i + batch]:
                ent_j, top_j = tag_unit(r["judul"], r["hierarchy"], r["isi"])
                conn.execute(
                    "UPDATE peraturan_unit SET entitas=?, topik=? WHERE id=?",
                    (ent_j, top_j, r["id"]))
                n += 1
            conn.commit()
            if progress:
                print("[peraturan_db] backfill tags: %d/%d" % (n, len(rows)),
                      flush=True)
    except Exception as e:
        return {"ok": False, "error": str(e)[:200], "n": n}
    finally:
        if own:
            conn.close()
    return {"ok": True, "n": n}


# --------------------------------------------------------------- Fase 2: relasi
def build_relasi(conn=None, progress=True):
    """Fase 2: bangun tabel peraturan_relasi dari status_terkait/history_terkait.

    Satu baris sumber per DOKUMEN (distinct source_id; kolom JSON diisi parser
    TKB):
      status_terkait  -> jenis 'penerus'   (dokumen LEBIH BARU yang
                                             mengubah/mencabut dokumen ini)
      history_terkait -> jenis 'pendahulu' (dokumen terkait yang lebih lama)
    Idempoten (UNIQUE(from_source,to_source,jenis_relasi) + INSERT OR IGNORE)."""
    own = conn is None
    conn = conn or init_db(connect())
    n_add = 0
    try:
        rows = conn.execute(
            "SELECT source_id, MAX(status_terkait) AS st, MAX(history_terkait) AS hs "
            "FROM peraturan_unit WHERE source_id IS NOT NULL AND TRIM(source_id)<>'' "
            "GROUP BY source_id").fetchall()
    except Exception as e:
        if own:
            conn.close()
        return {"ok": False, "error": str(e)[:200], "n": 0}
    try:
        for idx, r in enumerate(rows, start=1):
            sid = r["source_id"]
            for col, jenis in (("st", "penerus"), ("hs", "pendahulu")):
                try:
                    items = json.loads(r[col] or "[]")
                except Exception:
                    items = []
                if not isinstance(items, list):
                    continue
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    try:
                        cur = conn.execute(
                            "INSERT OR IGNORE INTO peraturan_relasi "
                            "(from_source, to_source, jenis_relasi, tanggal, "
                            " nomor_tujuan, judul_tujuan, link, deskripsi) "
                            "VALUES (?,?,?,?,?,?,?,?)",
                            (sid, str(it.get("source_id") or ""), jenis,
                             str(it.get("tanggal") or ""), str(it.get("nomor") or ""),
                             str(it.get("judul") or ""), str(it.get("link") or ""),
                             str(it.get("deskripsi") or "")))
                        n_add += cur.rowcount or 0
                    except Exception:
                        pass
            conn.commit()
            if progress and (idx % 200 == 0 or idx == len(rows)):
                print("[peraturan_db] build relasi: %d/%d dokumen (+%d relasi)"
                      % (idx, len(rows), n_add), flush=True)
    except Exception as e:
        return {"ok": False, "error": str(e)[:200], "n": n_add}
    finally:
        if own:
            conn.close()
    return {"ok": True, "n": n_add, "dokumen": len(rows)}


def trace_successor(source_id, maks_lompatan=3, conn=None):
    """Telusuri rantai 'penerus' dari source_id sampai dokumen berstatus
    'berlaku' atau batas lompatan tercapai (multi-hop).

    Kembalikan list langkah berurut:
      [{source_id, nomor, judul, tanggal, status}]
    Langkah terakhir idealnya dokumen pengganti yang berlaku. List kosong bila
    tabel relasi belum dibangun / rantai tak ada."""
    out = []
    if not source_id:
        return out
    own = conn is None
    conn = conn or init_db(connect())
    try:
        cur_sid = source_id
        seen = {source_id}
        for _ in range(max(1, int(maks_lompatan))):
            try:
                r = conn.execute(
                    "SELECT to_source, nomor_tujuan, judul_tujuan, tanggal "
                    "FROM peraturan_relasi "
                    "WHERE from_source=? AND jenis_relasi='penerus' "
                    "ORDER BY tanggal DESC, id DESC LIMIT 1", (cur_sid,)).fetchone()
            except Exception:
                r = None
            if not r:
                break
            nxt = str(r["to_source"] or "").strip()
            if not nxt or nxt in seen:
                break
            seen.add(nxt)
            try:
                info = induk_info(nxt, conn=conn) or {}
            except Exception:
                info = {}
            out.append({"source_id": nxt,
                        "nomor": str(r["nomor_tujuan"] or ""),
                        "judul": str(r["judul_tujuan"] or info.get("judul") or ""),
                        "tanggal": str(r["tanggal"] or ""),
                        "status": str(info.get("status") or "")})
            if str(info.get("status") or "").lower() == "berlaku":
                break
            cur_sid = nxt
    except Exception:
        pass
    finally:
        if own:
            conn.close()
    return out


# --------------------------------------------------------------------- upsert
def _norm(data):
    out = {}
    for k in PERATURAN_KOLOM:
        v = data.get(k)
        if isinstance(v, str) and v.strip() == "":
            v = None
        if k in _INT_FIELDS and v is not None and v != "":
            try:
                v = int(v)
            except Exception:
                v = None
        out[k] = v
    if not out.get("id"):
        raise ValueError("peraturan wajib punya 'id'")
    if out.get("status") is None:
        out["status"] = "berlaku"
    if out.get("can_cite") is None:
        out["can_cite"] = 1
    return out


def _sync_fts(conn, id_, judul, isi, hierarchy="", nomor=""):
    """Sinkronkan satu baris ke indeks FTS; format konten mengikuti versi tabel:
    v3 -> +kolom nomor (normalisasi ringan); v2 -> ternormalisasi 4 kolom;
    v1 -> mentah 3 kolom (legacy)."""
    if not _fts_available(conn):
        return
    try:
        conn.execute("DELETE FROM peraturan_fts WHERE id = ?", (id_,))
        ver = _fts_ver(conn)
        if ver == "3":
            conn.execute(
                "INSERT INTO peraturan_fts(id, nomor, judul, hierarchy, isi) "
                "VALUES (?, ?, ?, ?, ?)",
                (id_, _light_norm(nomor), _norm_text(judul),
                 _norm_text(hierarchy), _norm_text(isi)),
            )
        elif ver == "2":
            conn.execute(
                "INSERT INTO peraturan_fts(id, judul, hierarchy, isi) "
                "VALUES (?, ?, ?, ?)",
                (id_, _norm_text(judul), _norm_text(hierarchy), _norm_text(isi)),
            )
        else:
            conn.execute(
                "INSERT INTO peraturan_fts(id, judul, isi) VALUES (?, ?, ?)",
                (id_, judul or "", isi or ""),
            )
    except Exception:
        pass


def _sync_vec(conn, id_, teks):
    try:
        conn.execute("DELETE FROM peraturan_vec WHERE id = ?", (id_,))
    except Exception:
        return False
    if not (teks or "").strip():
        _vec_cache_clear()
        return False
    vec = psem.embed_passage(teks)
    if vec is None:
        _vec_cache_clear()
        return False
    blob = psem.to_blob(vec)
    if blob is None:
        return False
    conn.execute(
        "INSERT INTO peraturan_vec(id, dim, emb) VALUES (?, ?, ?)",
        (id_, int(len(vec)), blob),
    )
    _vec_cache_clear()
    return True


def upsert_peraturan(data, conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        d = _norm(data)
        # Fase 2: auto-tag entitas/topik bila belum diisi eksplisit.
        try:
            if d.get("isi") and (not d.get("entitas") or not d.get("topik")):
                ent_j, top_j = tag_unit(d.get("judul"), d.get("hierarchy"), d.get("isi"))
                if not d.get("entitas"):
                    d["entitas"] = ent_j
                if not d.get("topik"):
                    d["topik"] = top_j
        except Exception:
            pass
        cols = PERATURAN_KOLOM
        ph = ",".join("?" for _ in cols)
        updates = ",".join("%s=excluded.%s" % (c, c) for c in cols if c != "id")
        conn.execute(
            "INSERT INTO peraturan_unit(%s) VALUES (%s) "
            "ON CONFLICT(id) DO UPDATE SET %s, updated_at=datetime('now')"
            % (",".join(cols), ph, updates),
            tuple(d[c] for c in cols),
        )
        _sync_fts(conn, d["id"], d.get("judul"), d.get("isi"), d.get("hierarchy"),
                  nomor="%s %s" % (d.get("jenis_peraturan") or "", d.get("nomor") or ""))
        vec_ok = _sync_vec(conn, d["id"], "%s %s" % (d.get("judul") or "", d.get("isi") or ""))
        conn.commit()
        return {"id": d["id"], "vec_ok": vec_ok}
    finally:
        if own:
            conn.close()


def delete_peraturan(id_, conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        conn.execute("DELETE FROM peraturan_unit WHERE id = ?", (id_,))
        if _fts_available(conn):
            conn.execute("DELETE FROM peraturan_fts WHERE id = ?", (id_,))
        conn.execute("DELETE FROM peraturan_vec WHERE id = ?", (id_,))
        conn.commit()
        _vec_cache_clear()
    finally:
        if own:
            conn.close()


# ----------------------------------------------------------------------- read
def get_peraturan(id_, conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        r = conn.execute("SELECT * FROM peraturan_unit WHERE id = ?", (id_,)).fetchone()
        return dict(r) if r else None
    finally:
        if own:
            conn.close()


def induk_info(source_id, conn=None):
    """Identitas peraturan INDUK dari DB berdasarkan source_id.

    Dipakai batch untuk menautkan lampiran yang diproses pada run TERPISAH
    (mis. folder khusus OCR) ke peraturan induk yang SUDAH lebih dulu diimpor,
    tanpa perlu induk HTML ikut di folder yang sama. Baris non-lampiran (dan
    yang punya pasal) diprioritaskan sebagai perwakilan identitas. Kembalikan
    dict ringkas (termasuk status_terkait/history_terkait yang sudah berupa
    JSON string siap pakai) atau None bila tak ada.
    """
    if not source_id:
        return None
    own = conn is None
    conn = conn or init_db(connect())
    try:
        r = conn.execute(
            "SELECT id, jenis_peraturan, nomor, tahun, judul, valid_from, status, "
            "       status_terkait, history_terkait, source_url "
            "FROM peraturan_unit WHERE source_id = ? "
            "ORDER BY (CASE WHEN lampiran IS NULL OR TRIM(lampiran)='' THEN 0 ELSE 1 END), "
            "         (CASE WHEN pasal IS NOT NULL AND TRIM(pasal)<>'' THEN 0 ELSE 1 END) "
            "LIMIT 1",
            (source_id,),
        ).fetchone()
        return dict(r) if r else None
    finally:
        if own:
            conn.close()


def list_sumber(conn=None):
    """Ringkasan sumber yang sudah terindeks: satu baris per (source_file, source_id).

    Dipakai fitur rekonsiliasi/audit (peraturan_batch.audit_folder) untuk
    mengecek berkas mana di folder yang SUDAH atau BELUM ada di DB. Kolom
    is_lampiran menandai apakah baris berupa lampiran (1) atau bukan (0);
    baris non-lampiran diutamakan sebagai perwakilan identitas induk.
    """
    own = conn is None
    conn = conn or init_db(connect())
    try:
        rows = conn.execute(
            "SELECT source_file, source_id, "
            "MIN(jenis_peraturan) AS jenis_peraturan, MIN(nomor) AS nomor, "
            "MIN(judul) AS judul, MIN(status) AS status, "
            "MAX(CASE WHEN lampiran IS NOT NULL AND TRIM(lampiran)<>'' "
            "    THEN 1 ELSE 0 END) AS is_lampiran "
            "FROM peraturan_unit "
            "GROUP BY source_file, source_id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if own:
            conn.close()


def _is_lampiran(u):
    return bool((u.get("lampiran") or "").strip()) and not (u.get("pasal") or "").strip()


def _pasal_key(u):
    is_lamp = 1 if _is_lampiran(u) else 0
    m = re.match(r"(\d+)([A-Za-z]*)", str(u.get("pasal") or ""))
    num = int(m.group(1)) if m else 10 ** 9
    suf = m.group(2) if m else ""
    ma = re.match(r"(\d+)([a-z]*)", str(u.get("ayat") or ""))
    ay = int(ma.group(1)) if ma else 0
    return (is_lamp, num, suf, ay, str(u.get("id") or ""))


def peraturan_tersusun(nomor, jenis=None, conn=None):
    """Semua unit satu peraturan, terurut: pasal/ayat lalu lampiran di bawah."""
    own = conn is None
    conn = conn or init_db(connect())
    try:
        if jenis:
            rows = conn.execute(
                "SELECT * FROM peraturan_unit WHERE nomor=? AND jenis_peraturan=?",
                (nomor, jenis),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM peraturan_unit WHERE nomor=?", (nomor,)
            ).fetchall()
        return sorted((dict(r) for r in rows), key=_pasal_key)
    finally:
        if own:
            conn.close()


def list_peraturan_grouped(q="", jenis="", status="", lampiran="",
                           limit=200, offset=0, conn=None):
    """Daftar peraturan (dikelompokkan per jenis+nomor) dengan filter + paging."""
    own = conn is None
    conn = conn or init_db(connect())
    try:
        where, args = [], []
        if q:
            where.append("(nomor LIKE ? OR judul LIKE ?)")
            args += ["%" + q + "%", "%" + q + "%"]
        if jenis:
            where.append("jenis_peraturan = ?")
            args.append(jenis)
        if status:
            where.append("status = ?")
            args.append(status)
        wsql = ("WHERE " + " AND ".join(where)) if where else ""
        having = ""
        if lampiran == "ada":
            having = "HAVING n_lampiran > 0"
        elif lampiran == "tidak":
            having = "HAVING n_lampiran = 0"
        base = (
            "SELECT jenis_peraturan, nomor, MAX(tahun) AS tahun, "
            "COUNT(*) AS n_unit, "
            "SUM(CASE WHEN lampiran IS NOT NULL AND TRIM(lampiran)<>'' "
            "    THEN 1 ELSE 0 END) AS n_lampiran, "
            "MIN(status) AS status, MIN(judul) AS judul, "
            "MIN(source_id) AS source_id, MAX(source_url) AS source_url "
            "FROM peraturan_unit " + wsql + " "
            "GROUP BY jenis_peraturan, nomor " + having
        )
        all_rows = conn.execute(base, tuple(args)).fetchall()
        total = len(all_rows)
        items = [dict(r) for r in all_rows]
        items.sort(key=lambda r: (-(r["tahun"] or 0), r["jenis_peraturan"] or "", r["nomor"] or ""))
        items = items[offset: offset + limit]
        return {"items": items, "total": total}
    finally:
        if own:
            conn.close()


# --------------------------------------------------------------- update status
def bulk_update_status(status, keys=None, source_ids=None, extra=None, conn=None):
    if status not in ("berlaku", "dicabut", "diubah"):
        raise ValueError("status tidak valid: %s" % status)
    own = conn is None
    conn = conn or init_db(connect())
    try:
        extra = extra or {}
        set_cols = ["status = ?"]
        set_args = [status]
        for k in ("dicabut_oleh", "diubah_oleh", "jenis_perubahan", "valid_to"):
            if extra.get(k) is not None:
                set_cols.append("%s = ?" % k)
                set_args.append(extra[k])
        set_sql = ", ".join(set_cols) + ", updated_at = datetime('now')"
        n_unit = n_per = 0
        for sid in (source_ids or []):
            cur = conn.execute(
                "UPDATE peraturan_unit SET %s WHERE source_id = ?" % set_sql,
                (*set_args, sid),
            )
            if cur.rowcount:
                n_unit += cur.rowcount
                n_per += 1
        for k in (keys or []):
            cur = conn.execute(
                "UPDATE peraturan_unit SET %s WHERE jenis_peraturan = ? AND nomor = ?" % set_sql,
                (*set_args, k.get("jenis") or k.get("jenis_peraturan"), k["nomor"]),
            )
            if cur.rowcount:
                n_unit += cur.rowcount
                n_per += 1
        conn.commit()
        return {"status": status, "unit_diubah": n_unit, "peraturan_diubah": n_per}
    finally:
        if own:
            conn.close()


def bulk_delete(keys, conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        n = 0
        for k in keys:
            ids = [r["id"] for r in conn.execute(
                "SELECT id FROM peraturan_unit WHERE jenis_peraturan=? AND nomor=?",
                (k.get("jenis") or k.get("jenis_peraturan"), k["nomor"]),
            ).fetchall()]
            for id_ in ids:
                conn.execute("DELETE FROM peraturan_unit WHERE id = ?", (id_,))
                if _fts_available(conn):
                    conn.execute("DELETE FROM peraturan_fts WHERE id = ?", (id_,))
                conn.execute("DELETE FROM peraturan_vec WHERE id = ?", (id_,))
                n += 1
        conn.commit()
        _vec_cache_clear()
        return {"unit_dihapus": n}
    finally:
        if own:
            conn.close()


# ---------------------------------------------------------------------- impor
def upsert_impor_log(rows, conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        n = 0
        for r in rows:
            vals = tuple(r.get(k) for k in IMPOR_KOLOM)
            ph = ",".join("?" for _ in IMPOR_KOLOM)
            upd = ",".join("%s=excluded.%s" % (c, c) for c in IMPOR_KOLOM if c != "file")
            conn.execute(
                "INSERT INTO impor_log(%s) VALUES (%s) "
                "ON CONFLICT(file) DO UPDATE SET %s, ts=datetime('now')"
                % (",".join(IMPOR_KOLOM), ph, upd),
                vals,
            )
            n += 1
        conn.commit()
        return n
    finally:
        if own:
            conn.close()


def list_impor_log(status="", limit=500, conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM impor_log WHERE status = ? ORDER BY kategori, file LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM impor_log ORDER BY kategori, file LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if own:
            conn.close()


# ---------------------------------------------------------------------- stats
def stats(conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        def _c(sql, args=()):
            return conn.execute(sql, args).fetchone()[0] or 0

        LAMP = "lampiran IS NOT NULL AND TRIM(lampiran)<>''"
        KEY = "jenis_peraturan || '|' || nomor"
        out = {
            "total_peraturan": _c("SELECT COUNT(DISTINCT %s) FROM peraturan_unit" % KEY),
            "total_unit": _c("SELECT COUNT(*) FROM peraturan_unit"),
            "total_pasal": _c("SELECT COUNT(*) FROM peraturan_unit WHERE pasal IS NOT NULL AND TRIM(pasal)<>''"),
            "total_lampiran_unit": _c("SELECT COUNT(*) FROM peraturan_unit WHERE %s" % LAMP),
            "total_vec": _c("SELECT COUNT(*) FROM peraturan_vec"),
        }
        srow = conn.execute("SELECT status, COUNT(*) AS n FROM peraturan_unit GROUP BY status").fetchall()
        out["status"] = {r["status"]: r["n"] for r in srow}
        try:
            out["total_relasi"] = _c("SELECT COUNT(*) FROM peraturan_relasi")
            out["unit_bertag"] = _c("SELECT COUNT(*) FROM peraturan_unit WHERE entitas IS NOT NULL")
        except Exception:
            pass
        has_log = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='impor_log'"
        ).fetchone()
        triase = {}
        if has_log:
            frow = conn.execute("SELECT status, COUNT(*) AS n FROM impor_log GROUP BY status").fetchall()
            triase = {r["status"]: r["n"] for r in frow}
        out["triase"] = triase
        return out
    finally:
        if own:
            conn.close()


# ------------------------------------------------------------------ retrieval
def _fts_query(text):
    toks = re.findall(r"\w+", text or "", flags=re.UNICODE)
    if not toks:
        return None
    return " OR ".join('"%s"' % t for t in toks)


def _like_ids(conn, query, limit=50):
    toks = re.findall(r"[0-9A-Za-z]{3,}", (query or "").lower())[:8]
    if not toks:
        return []
    where = " OR ".join(["LOWER(judul) LIKE ? OR LOWER(isi) LIKE ?"] * len(toks))
    params = []
    for t in toks:
        params += ["%" + t + "%", "%" + t + "%"]
    try:
        rows = conn.execute(
            "SELECT id FROM peraturan_unit WHERE " + where + " LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [r["id"] for r in rows]
    except Exception:
        return []


def _fts_ids(conn, query, limit=50):
    if not _fts_available(conn):
        return _like_ids(conn, query, limit)
    ver = _fts_ver(conn)
    if ver in ("2", "3"):
        # Query token TERNORMALISASI + bm25 berbobot kolom.
        try:
            toks = tnorm.norm_tokens(query, k=16) if tnorm is not None else []
        except Exception:
            toks = []
        if not toks:
            toks = re.findall(r"\w+", (query or "").lower())[:16]
        if toks:
            fq = " OR ".join('"%s"' % t for t in toks)
            try:
                if ver == "3":
                    # bobot kolom: id(diabaikan) 0, nomor 12x, judul 8x,
                    # hierarchy 4x, isi 1x — nomor exact paling dominan.
                    rows = conn.execute(
                        "SELECT id FROM peraturan_fts WHERE peraturan_fts MATCH ? "
                        "ORDER BY bm25(peraturan_fts, 0.0, 12.0, 8.0, 4.0, 1.0) LIMIT ?",
                        (fq, limit),
                    ).fetchall()
                else:
                    # v2: judul 10x, hierarchy 4x, isi 1x
                    rows = conn.execute(
                        "SELECT id FROM peraturan_fts WHERE peraturan_fts MATCH ? "
                        "ORDER BY bm25(peraturan_fts, 0.0, 10.0, 4.0, 1.0) LIMIT ?",
                        (fq, limit),
                    ).fetchall()
                return [r["id"] for r in rows]
            except Exception:
                pass
    fq = _fts_query(query)
    if not fq:
        return []
    try:
        rows = conn.execute(
            "SELECT id FROM peraturan_fts WHERE peraturan_fts MATCH ? ORDER BY rank LIMIT ?",
            (fq, limit),
        ).fetchall()
        return [r["id"] for r in rows]
    except Exception:
        return _like_ids(conn, query, limit)


def _vec_ids(conn, query, limit=50):
    if np is None:
        return []
    qv = psem.embed_query(query)
    if qv is None:
        return []
    ids, mat = _load_vectors(conn)
    if mat is None or not ids:
        return []
    try:
        sims = mat @ np.asarray(qv, dtype="float32")
        order = np.argsort(-sims)[:limit]
        return [ids[int(i)] for i in order]
    except Exception:
        return []


def _rrf(*ranked):
    scores = {}
    for lst in ranked:
        for rank, id_ in enumerate(lst, start=1):
            scores[id_] = scores.get(id_, 0.0) + 1.0 / (RRF_K + rank)
    return scores


def search(query, k=10, status_list=("berlaku",), conn=None):
    """Hybrid FTS5 + vektor e5, digabung RRF. Kembalikan list dict + 'skor'."""
    own = conn is None
    conn = conn or init_db(connect())
    try:
        q = (query or "").strip()
        if not q:
            return []
        fts = _fts_ids(conn, q)
        vec = _vec_ids(conn, q)
        scores = _rrf(fts, vec)
        if not scores:
            return []
        id_ph = ",".join("?" for _ in scores)
        st = list(status_list) if status_list else []
        if st:
            st_ph = ",".join("?" for _ in st)
            rows = conn.execute(
                "SELECT * FROM peraturan_unit WHERE id IN (%s) AND status IN (%s)" % (id_ph, st_ph),
                (*scores.keys(), *st),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM peraturan_unit WHERE id IN (%s)" % id_ph,
                tuple(scores.keys()),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["skor"] = scores.get(d["id"], 0.0)
            out.append(d)
        out.sort(key=lambda x: -x["skor"])
        return out[:k]
    finally:
        if own:
            conn.close()


def reindex(conn=None, batch=64, resume=True):
    """(Re)hitung embedding untuk baris peraturan_unit. Perlu model tersedia.

    resume=True (default) -> hanya proses baris yang BELUM punya vektor
        berdimensi model aktif (dibaca dari peraturan_vec.dim). Aman dilanjutkan
        setelah Ctrl+C/interupsi dan hemat waktu saat ganti model: unit yang
        sudah di-embed model baru dilewati.
    resume=False -> embed ulang SEMUA baris (mode penuh; dipakai --force).
    """
    own = conn is None
    conn = conn or init_db(connect())
    try:
        if not psem.is_available():
            return {"ok": False, "error": "Model embedding tidak tersedia.", "n": 0}
        rows = conn.execute("SELECT id, judul, isi FROM peraturan_unit").fetchall()
        skip = set()
        if resume:
            try:
                dim_model = int(psem.embed_dim() or 0)
            except Exception:
                dim_model = 0
            if dim_model:
                for r in conn.execute(
                        "SELECT id FROM peraturan_vec WHERE dim=?",
                        (dim_model,)).fetchall():
                    skip.add(r["id"])
        todo = [r for r in rows if r["id"] not in skip]
        ids = [r["id"] for r in todo]
        texts = [((r["judul"] or "") + " " + (r["isi"] or "")).strip() for r in todo]
        n = 0
        for i in range(0, len(ids), batch):
            chunk_ids = ids[i:i + batch]
            chunk_txt = texts[i:i + batch]
            arr = psem.embed_passages(chunk_txt)
            if arr is None:
                continue
            for j, id_ in enumerate(chunk_ids):
                v = arr[j]
                conn.execute("DELETE FROM peraturan_vec WHERE id=?", (id_,))
                conn.execute(
                    "INSERT INTO peraturan_vec(id, dim, emb) VALUES (?,?,?)",
                    (id_, int(len(v)), psem.to_blob(v)),
                )
                n += 1
            conn.commit()
        _vec_cache_clear()
        return {"ok": True, "n": n, "skipped": len(rows) - len(todo),
                "total": len(rows)}
    finally:
        if own:
            conn.close()
