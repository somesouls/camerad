# -*- coding: utf-8 -*-
"""rag_kamus_db.py — Kamus sinonim/istilah pajak untuk query rewriting (Tahap 5).

Tujuan: menjembatani vocabulary mismatch antara bahasa awam pengguna dan bahasa
hukum/formal pada korpus PERATURAN. Menyimpan pemetaan istilah baku (formal) ke
daftar sinonim/variasi awam, lalu dipakai rag_rewrite.expand_kamus() untuk
memperluas query sebelum retrieval hybrid (FTS5 + embedding).

Tabel `kamus_sinonim`:
  id         INTEGER PK
  istilah    TEXT  -- bentuk baku/formal (mis. "Pajak Pertambahan Nilai")
  sinonim    TEXT  -- JSON array variasi awam (mis. ["ppn","pajak jualan"])
  kategori   TEXT  -- pengelompokan bebas (mis. "PPN", "akronim")
  catatan    TEXT
  aktif      INTEGER DEFAULT 1
  created_at, updated_at TEXT

Seeding bawaan (Fase 0): seed_default() bekerja dalam mode MERGE idempoten —
hanya menambah entri bawaan yang 'istilah'-nya belum ada (case-insensitive)
dan TIDAK menimpa entri hasil suntingan admin. Catatan: entri bawaan yang
DIHAPUS akan ditambahkan ulang pada proses berikutnya; bila ingin mematikan
entri bawaan, set aktif=0 alih-alih menghapusnya.

Pola koneksi mengikuti modul *_db.py camerad lain (sqlite3 + WAL). Gagal-anggun:
semua fungsi baca mengembalikan nilai kosong bila DB/tabel bermasalah.
"""
import os
import re
import json
import sqlite3

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_BUSY_TIMEOUT_MS = 15000


def default_db_path():
    return os.environ.get("PIPELINE_KAMUS_DB_FILE") or os.path.join(_BASE_DIR, "rag_kamus.db")


def connect(db_path=None):
    conn = sqlite3.connect(db_path or default_db_path(), timeout=_BUSY_TIMEOUT_MS / 1000.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=%d;" % _BUSY_TIMEOUT_MS)
    return conn


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
        CREATE TABLE IF NOT EXISTS kamus_sinonim (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            istilah    TEXT NOT NULL,
            sinonim    TEXT,
            kategori   TEXT,
            catatan    TEXT,
            aktif      INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_kamus_aktif ON kamus_sinonim(aktif);
        """
    )
    conn.commit()
    if key:
        _INIT_DONE.add(key)
    try:
        seed_default(conn)
    except Exception:
        pass
    return conn


# --------------------------------------------------------------- cache
_CACHE = {"sig": None, "rows": None}


def _sig(conn):
    try:
        r = conn.execute("SELECT COUNT(*), COALESCE(MAX(updated_at),'') FROM kamus_sinonim").fetchone()
        return (int(r[0]), str(r[1]))
    except Exception:
        return (0, "")


def _cache_clear():
    _CACHE["sig"] = None
    _CACHE["rows"] = None


def _json_list(v):
    try:
        x = json.loads(v) if v else []
        return [str(t).strip() for t in x if str(t).strip()] if isinstance(x, list) else []
    except Exception:
        return []


# --------------------------------------------------------------- tulis
def _norm_sinonim(v):
    if isinstance(v, list):
        arr = [str(t).strip() for t in v if str(t).strip()]
    else:
        arr = [t.strip() for t in re.split(r"[,\n;]+", str(v or "")) if t.strip()]
    out = []
    for t in arr:
        if t.lower() not in [x.lower() for x in out]:
            out.append(t)
    return out


def upsert(data, conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        istilah = str(data.get("istilah") or "").strip()
        if not istilah:
            raise ValueError("field 'istilah' wajib diisi")
        sinonim = json.dumps(_norm_sinonim(data.get("sinonim")), ensure_ascii=False)
        kategori = str(data.get("kategori") or "").strip() or None
        catatan = str(data.get("catatan") or "").strip() or None
        aktif = 0 if str(data.get("aktif")) in ("0", "false", "False", "no") else 1
        idv = data.get("id")
        if idv:
            conn.execute(
                "UPDATE kamus_sinonim SET istilah=?, sinonim=?, kategori=?, catatan=?, "
                "aktif=?, updated_at=datetime('now') WHERE id=?",
                (istilah, sinonim, kategori, catatan, aktif, int(idv)),
            )
            new_id = int(idv)
        else:
            cur = conn.execute(
                "INSERT INTO kamus_sinonim(istilah, sinonim, kategori, catatan, aktif) "
                "VALUES (?,?,?,?,?)",
                (istilah, sinonim, kategori, catatan, aktif),
            )
            new_id = int(cur.lastrowid)
        conn.commit()
        _cache_clear()
        return {"id": new_id}
    finally:
        if own:
            conn.close()


def delete(id_, conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        conn.execute("DELETE FROM kamus_sinonim WHERE id=?", (int(id_),))
        conn.commit()
        _cache_clear()
        return {"dihapus": 1}
    finally:
        if own:
            conn.close()


# --------------------------------------------------------------- baca
def get(id_, conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        r = conn.execute("SELECT * FROM kamus_sinonim WHERE id=?", (int(id_),)).fetchone()
        return dict(r) if r else None
    finally:
        if own:
            conn.close()


def list_all(q="", limit=500, offset=0, conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        q = (q or "").strip()
        if q:
            rows = conn.execute(
                "SELECT * FROM kamus_sinonim WHERE istilah LIKE ? OR sinonim LIKE ? "
                "OR COALESCE(kategori,'') LIKE ? ORDER BY istilah LIMIT ? OFFSET ?",
                ("%" + q + "%", "%" + q + "%", "%" + q + "%", limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM kamus_sinonim ORDER BY istilah LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["sinonim_list"] = _json_list(d.get("sinonim"))
            out.append(d)
        return out
    finally:
        if own:
            conn.close()


def all_active(conn=None):
    """List entri aktif (dengan cache) sebagai [{istilah, forms:[...]}]."""
    own = conn is None
    conn = conn or init_db(connect())
    try:
        sig = _sig(conn)
        if _CACHE["sig"] == sig and _CACHE["rows"] is not None:
            return _CACHE["rows"]
        try:
            rows = conn.execute(
                "SELECT istilah, sinonim FROM kamus_sinonim WHERE COALESCE(aktif,1)=1"
            ).fetchall()
        except Exception:
            rows = []
        out = []
        for r in rows:
            d = dict(r)
            istilah = str(d.get("istilah") or "").strip()
            if not istilah:
                continue
            forms = [istilah] + _json_list(d.get("sinonim"))
            uniq = []
            for f in forms:
                if f and f.lower() not in [x.lower() for x in uniq]:
                    uniq.append(f)
            out.append({"istilah": istilah, "forms": uniq})
        _CACHE["sig"] = sig
        _CACHE["rows"] = out
        return out
    finally:
        if own:
            conn.close()


def stats(conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        n = conn.execute("SELECT COUNT(*) FROM kamus_sinonim").fetchone()[0] or 0
        na = conn.execute("SELECT COUNT(*) FROM kamus_sinonim WHERE COALESCE(aktif,1)=1").fetchone()[0] or 0
        return {"total": int(n), "aktif": int(na)}
    finally:
        if own:
            conn.close()


# --------------------------------------------------------------- expand
def _has_term(text_low, term):
    t = (term or "").strip().lower()
    if not t:
        return False
    return re.search(r"(?<![0-9a-z])" + re.escape(t) + r"(?![0-9a-z])", text_low) is not None


def expand_terms(text, maks=12, conn=None):
    """Kembalikan daftar istilah tambahan untuk memperluas query.

    Untuk tiap entri: bila SALAH SATU bentuk (istilah/sinonim) muncul di teks,
    tambahkan bentuk lain yang BELUM ada di teks. Dipakai rag_rewrite.
    """
    text_low = (text or "").lower()
    if not text_low.strip():
        return []
    try:
        entries = all_active(conn=conn)
    except Exception:
        entries = []
    extra = []
    for e in entries:
        forms = e.get("forms") or []
        if not any(_has_term(text_low, f) for f in forms):
            continue
        for f in forms:
            if not _has_term(text_low, f) and f.lower() not in [x.lower() for x in extra]:
                extra.append(f)
        if len(extra) >= maks:
            break
    return extra[:maks]


# --------------------------------------------------------------- seed
# Fase 0: diperkaya dari 12 -> 115 entri, mencakup akronim DJP, istilah awam,
# objek pajak, jenis PPh, aplikasi, proses, sanksi, dan istilah kepabeanan
# (kawasan berikat, luar daerah pabean, dst.).
_DEFAULT_SEED = [
    # --- akronim umum ---
    {"istilah": "Pajak Pertambahan Nilai", "sinonim": ["PPN", "pajak jualan", "pajak pertambahan"], "kategori": "akronim"},
    {"istilah": "Pajak Penghasilan", "sinonim": ["PPh", "pajak gaji", "pajak penghasilan"], "kategori": "akronim"},
    {"istilah": "Pengusaha Kena Pajak", "sinonim": ["PKP"], "kategori": "akronim"},
    {"istilah": "Nomor Pokok Wajib Pajak", "sinonim": ["NPWP"], "kategori": "akronim"},
    {"istilah": "Wajib Pajak", "sinonim": ["WP"], "kategori": "akronim"},
    {"istilah": "Surat Pemberitahuan", "sinonim": ["SPT", "lapor pajak", "laporan pajak"], "kategori": "akronim"},
    {"istilah": "Surat Ketetapan Pajak", "sinonim": ["SKP"], "kategori": "akronim"},
    {"istilah": "Bea Perolehan Hak atas Tanah dan Bangunan", "sinonim": ["BPHTB"], "kategori": "akronim"},
    {"istilah": "Pajak Bumi dan Bangunan", "sinonim": ["PBB"], "kategori": "akronim"},
    {"istilah": "Subjek Pajak Luar Negeri", "sinonim": ["SPLN", "subjek pajak luar negeri", "wajib pajak luar negeri", "pihak luar negeri"], "kategori": "akronim"},
    {"istilah": "Subjek Pajak Dalam Negeri", "sinonim": ["SPDN", "subjek pajak dalam negeri"], "kategori": "akronim"},
    {"istilah": "Bentuk Usaha Tetap", "sinonim": ["BUT", "permanent establishment"], "kategori": "akronim"},
    {"istilah": "Barang Kena Pajak", "sinonim": ["BKP"], "kategori": "akronim"},
    {"istilah": "Jasa Kena Pajak", "sinonim": ["JKP"], "kategori": "akronim"},
    {"istilah": "Dasar Pengenaan Pajak", "sinonim": ["DPP", "dasar kena pajak"], "kategori": "akronim"},
    {"istilah": "Pajak Penjualan atas Barang Mewah", "sinonim": ["PPnBM", "pajak barang mewah", "barang mewah"], "kategori": "akronim"},
    {"istilah": "Nomor Transaksi Penerimaan Negara", "sinonim": ["NTPN", "bukti bayar pajak", "bukti pembayaran"], "kategori": "akronim"},
    {"istilah": "Electronic Filing Identification Number", "sinonim": ["EFIN", "kode efin", "nomor efin"], "kategori": "akronim"},
    {"istilah": "Nomor Identitas Tempat Kegiatan Usaha", "sinonim": ["NITKU"], "kategori": "akronim"},
    {"istilah": "Surat Tagihan Pajak", "sinonim": ["STP"], "kategori": "akronim"},
    {"istilah": "Surat Ketetapan Pajak Kurang Bayar", "sinonim": ["SKPKB"], "kategori": "akronim"},
    {"istilah": "Surat Ketetapan Pajak Lebih Bayar", "sinonim": ["SKPLB"], "kategori": "akronim"},
    {"istilah": "Surat Ketetapan Pajak Nihil", "sinonim": ["SKPN"], "kategori": "akronim"},
    {"istilah": "Surat Keterangan Domisili", "sinonim": ["SKD", "certificate of residence", "COR", "DGT"], "kategori": "akronim"},
    {"istilah": "Persetujuan Penghindaran Pajak Berganda", "sinonim": ["P3B", "tax treaty", "perjanjian pajak", "penghindaran pajak berganda"], "kategori": "akronim"},
    {"istilah": "Penghasilan Tidak Kena Pajak", "sinonim": ["PTKP"], "kategori": "akronim"},
    {"istilah": "Norma Penghitungan Penghasilan Neto", "sinonim": ["NPPN", "norma penghitungan"], "kategori": "akronim"},
    {"istilah": "Pemberitahuan Impor Barang", "sinonim": ["PIB", "dokumen impor"], "kategori": "akronim"},
    {"istilah": "Pemberitahuan Ekspor Barang", "sinonim": ["PEB", "dokumen ekspor"], "kategori": "akronim"},
    {"istilah": "Kawasan Ekonomi Khusus", "sinonim": ["KEK"], "kategori": "akronim"},
    {"istilah": "Kawasan Perdagangan Bebas dan Pelabuhan Bebas", "sinonim": ["KPBPB", "free trade zone", "FTZ", "kawasan bebas"], "kategori": "akronim"},
    {"istilah": "Tempat Penimbunan Berikat", "sinonim": ["TPB", "tempat penimbunan"], "kategori": "akronim"},
    {"istilah": "Perdagangan Melalui Sistem Elektronik", "sinonim": ["PMSE", "pajak digital", "pajak perusahaan digital"], "kategori": "akronim"},
    {"istilah": "Laporan per Negara", "sinonim": ["CbCR", "country by country report"], "kategori": "akronim"},
    {"istilah": "Konfirmasi Status Wajib Pajak", "sinonim": ["KSWP"], "kategori": "akronim"},
    {"istilah": "Nomor Induk Kependudukan", "sinonim": ["NIK", "nomor ktp"], "kategori": "akronim"},
    {"istilah": "Surat Permintaan Data dan Keterangan", "sinonim": ["SP2DK", "surat himbauan", "himbauan pajak"], "kategori": "akronim"},
    {"istilah": "Penerimaan Negara Bukan Pajak", "sinonim": ["PNBP"], "kategori": "akronim"},
    {"istilah": "Tunjangan Hari Raya", "sinonim": ["THR"], "kategori": "akronim"},
    # --- istilah umum ---
    {"istilah": "restitusi", "sinonim": ["pengembalian pajak", "minta balik pajak", "refund pajak", "pengembalian kelebihan bayar"], "kategori": "istilah"},
    {"istilah": "faktur pajak", "sinonim": ["invoice pajak", "nota pajak", "faktur"], "kategori": "istilah"},
    {"istilah": "lebih bayar", "sinonim": ["kelebihan bayar", "LB"], "kategori": "istilah"},
    {"istilah": "kurang bayar", "sinonim": ["kekurangan bayar", "KB"], "kategori": "istilah"},
    {"istilah": "nihil", "sinonim": ["spt nihil", "laporan nihil"], "kategori": "istilah"},
    {"istilah": "kompensasi kerugian", "sinonim": ["kompensasi", "kerugian fiskal", "ganti rugi pajak"], "kategori": "istilah"},
    {"istilah": "bukti potong", "sinonim": ["bupot", "bukti pemotongan", "bukti pungut"], "kategori": "istilah"},
    {"istilah": "pemotongan pajak", "sinonim": ["dipotong pajak", "withholding tax", "potong pajak"], "kategori": "istilah"},
    {"istilah": "pemungutan pajak", "sinonim": ["dipungut pajak", "pungut pajak", "memungut"], "kategori": "istilah"},
    {"istilah": "setor sendiri", "sinonim": ["bayar sendiri", "mekanisme setor sendiri"], "kategori": "istilah"},
    {"istilah": "pengukuhan Pengusaha Kena Pajak", "sinonim": ["pengukuhan pkp", "dikukuhkan", "jadi pkp"], "kategori": "istilah"},
    {"istilah": "pemusatan tempat terutang", "sinonim": ["pemusatan", "tempat terutang pusat"], "kategori": "istilah"},
    {"istilah": "pemadanan NIK sebagai NPWP", "sinonim": ["nik jadi npwp", "pemadanan nik", "integrasi nik npwp"], "kategori": "istilah"},
    {"istilah": "pengungkapan sukarela", "sinonim": ["program pengungkapan sukarela", "PPS", "tax amnesty", "amnesti pajak", "pengampunan pajak"], "kategori": "istilah"},
    {"istilah": "harga transfer", "sinonim": ["transfer pricing", "transaksi afiliasi", "hubungan istimewa"], "kategori": "istilah"},
    {"istilah": "dokumentasi harga transfer", "sinonim": ["tp doc", "transfer pricing documentation", "dokumen induk", "dokumen lokal"], "kategori": "istilah"},
    {"istilah": "aset kripto", "sinonim": ["kripto", "crypto", "bitcoin", "mata uang kripto"], "kategori": "istilah"},
    {"istilah": "pajak karbon", "sinonim": ["carbon tax"], "kategori": "istilah"},
    {"istilah": "uang elektronik", "sinonim": ["e-money", "e-wallet", "dompet digital"], "kategori": "istilah"},
    {"istilah": "wajib pajak orang pribadi", "sinonim": ["orang pribadi", "OP", "perorangan", "wajib pajak pribadi"], "kategori": "istilah"},
    {"istilah": "wajib pajak badan", "sinonim": ["badan", "perusahaan", "PT", "CV", "firma"], "kategori": "istilah"},
    {"istilah": "penyerahan Barang Kena Pajak", "sinonim": ["penyerahan BKP", "penjualan BKP", "serah barang"], "kategori": "istilah"},
    {"istilah": "pemanfaatan jasa dari luar Daerah Pabean", "sinonim": ["jasa dari luar negeri", "jasa luar negeri", "pakai jasa luar negeri"], "kategori": "istilah"},
    {"istilah": "Daerah Pabean", "sinonim": ["wilayah indonesia", "wilayah pabean"], "kategori": "kepabeanan"},
    {"istilah": "luar Daerah Pabean", "sinonim": ["luar negeri", "dari luar negeri", "di luar daerah pabean"], "kategori": "kepabeanan"},
    {"istilah": "Kawasan Berikat", "sinonim": ["bonded zone", "gudang berikat", "kawasan berikat bea cukai"], "kategori": "kepabeanan"},
    {"istilah": "pemasukan barang ke Kawasan Berikat", "sinonim": ["masuk kawasan berikat", "kirim ke kawasan berikat", "pemasukan ke kawasan berikat"], "kategori": "kepabeanan"},
    {"istilah": "pengeluaran barang dari Kawasan Berikat", "sinonim": ["keluar kawasan berikat", "pengeluaran kawasan berikat"], "kategori": "kepabeanan"},
    {"istilah": "fasilitas tidak dipungut", "sinonim": ["tidak dipungut PPN", "dibebaskan", "fasilitas PPN", "fasilitas pajak"], "kategori": "istilah"},
    {"istilah": "impor", "sinonim": ["impor barang", "barang impor", "mendatangkan barang"], "kategori": "istilah"},
    {"istilah": "ekspor", "sinonim": ["ekspor barang", "barang ekspor", "kirim ke luar negeri"], "kategori": "istilah"},
    # --- objek pajak ---
    {"istilah": "jasa angkutan laut", "sinonim": ["jasa pelayaran", "jasa kapal", "pelayaran"], "kategori": "objek"},
    {"istilah": "jasa konstruksi", "sinonim": ["konstruksi", "jasa bangunan", "proyek bangunan"], "kategori": "objek"},
    {"istilah": "sewa tanah dan bangunan", "sinonim": ["sewa gedung", "sewa ruko", "sewa tempat", "sewa kantor"], "kategori": "objek"},
    {"istilah": "royalti", "sinonim": ["royalty", "royalti hak cipta", "royalti lagu"], "kategori": "objek"},
    {"istilah": "dividen", "sinonim": ["dividen saham", "bagi hasil", "pembagian laba"], "kategori": "objek"},
    {"istilah": "bunga", "sinonim": ["bunga pinjaman", "bunga deposito", "bunga tabungan"], "kategori": "objek"},
    {"istilah": "hadiah dan undian", "sinonim": ["hadiah", "undian", "lotre", "doorprize"], "kategori": "objek"},
    {"istilah": "warisan", "sinonim": ["waris", "harta warisan", "harta waris"], "kategori": "objek"},
    {"istilah": "hibah", "sinonim": ["hibah orang tua", "pemberian", "sumbangan"], "kategori": "objek"},
    {"istilah": "beasiswa", "sinonim": ["beasiswa pendidikan", "scholarship"], "kategori": "objek"},
    {"istilah": "pesangon", "sinonim": ["uang pesangon", "phk", "pemutusan hubungan kerja"], "kategori": "objek"},
    {"istilah": "bonus dan tantiem", "sinonim": ["bonus karyawan", "tantiem", "bonus tahunan"], "kategori": "objek"},
    {"istilah": "iuran pensiun", "sinonim": ["dana pensiun", "pensiun"], "kategori": "objek"},
    # --- jenis PPh ---
    {"istilah": "PPh Pasal 21", "sinonim": ["pph 21", "pajak karyawan", "pajak gaji", "pemotongan gaji", "pajak penghasilan karyawan"], "kategori": "jenis_pajak"},
    {"istilah": "PPh Pasal 22", "sinonim": ["pph 22", "pajak impor", "pungutan impor"], "kategori": "jenis_pajak"},
    {"istilah": "PPh Pasal 23", "sinonim": ["pph 23", "pajak jasa", "pajak royalti", "pajak dividen", "pajak bunga"], "kategori": "jenis_pajak"},
    {"istilah": "PPh Pasal 25", "sinonim": ["pph 25", "angsuran pajak", "cicilan pajak", "angsuran bulanan"], "kategori": "jenis_pajak"},
    {"istilah": "PPh Pasal 26", "sinonim": ["pph 26", "pajak penghasilan luar negeri", "pembayaran ke luar negeri", "pajak spln"], "kategori": "jenis_pajak"},
    {"istilah": "PPh Pasal 29", "sinonim": ["pph 29", "kurang bayar tahunan", "kekurangan tahunan"], "kategori": "jenis_pajak"},
    {"istilah": "PPh Pasal 4 ayat 2", "sinonim": ["pph final", "pajak final", "pph 4(2)", "pph pasal 4 ayat 2"], "kategori": "jenis_pajak"},
    {"istilah": "PPh Pasal 15", "sinonim": ["pph 15", "pajak pelayaran", "pajak penerbangan"], "kategori": "jenis_pajak"},
    {"istilah": "PPh final UMKM", "sinonim": ["pp 23", "tarif umkm", "pajak umkm", "pajak 0.5 persen", "pajak online shop"], "kategori": "jenis_pajak"},
    # --- aplikasi ---
    {"istilah": "e-Bupot", "sinonim": ["ebupot", "bukti potong elektronik"], "kategori": "aplikasi"},
    {"istilah": "e-Faktur", "sinonim": ["efaktur", "faktur elektronik", "aplikasi faktur"], "kategori": "aplikasi"},
    {"istilah": "e-Billing", "sinonim": ["ebilling", "kode billing", "bayar pajak online", "bikin kode billing"], "kategori": "aplikasi"},
    {"istilah": "e-Filing", "sinonim": ["efiling", "lapor online", "lapor spt online"], "kategori": "aplikasi"},
    {"istilah": "e-Registration", "sinonim": ["ereg", "daftar npwp online", "registrasi online"], "kategori": "aplikasi"},
    {"istilah": "Coretax", "sinonim": ["coretax djp", "sistem coretax", "coretaxdjp"], "kategori": "aplikasi"},
    {"istilah": "DJP Online", "sinonim": ["djponline", "djp online", "situs pajak"], "kategori": "aplikasi"},
    {"istilah": "e-Meterai", "sinonim": ["emeterai", "meterai elektronik"], "kategori": "aplikasi"},
    {"istilah": "Kring Pajak", "sinonim": ["1500200", "call center pajak", "telepon pajak"], "kategori": "aplikasi"},
    # --- proses hukum ---
    {"istilah": "pemeriksaan pajak", "sinonim": ["audit pajak", "diperiksa pajak", "pemeriksaan"], "kategori": "proses"},
    {"istilah": "keberatan", "sinonim": ["ajukan keberatan", "keberatan pajak"], "kategori": "proses"},
    {"istilah": "banding", "sinonim": ["banding pajak", "pengadilan pajak", "naik banding"], "kategori": "proses"},
    {"istilah": "gugatan", "sinonim": ["gugatan pajak", "menggugat"], "kategori": "proses"},
    {"istilah": "peninjauan kembali", "sinonim": ["PK", "peninjauan kembali MA"], "kategori": "proses"},
    {"istilah": "penagihan pajak", "sinonim": ["surat paksa", "juru sita", "penagihan"], "kategori": "proses"},
    {"istilah": "pembetulan SPT", "sinonim": ["spt pembetulan", "betulkan spt", "perbaikan laporan"], "kategori": "proses"},
    # --- sanksi ---
    {"istilah": "sanksi administrasi", "sinonim": ["denda pajak", "denda keterlambatan", "sanksi denda", "telat lapor"], "kategori": "sanksi"},
    {"istilah": "bunga keterlambatan", "sinonim": ["bunga sanksi", "bunga pajak", "bunga per bulan"], "kategori": "sanksi"},
    {"istilah": "sanksi kenaikan", "sinonim": ["kenaikan", "sanksi 50 persen", "sanksi 100 persen"], "kategori": "sanksi"},
    # --- lainnya ---
    {"istilah": "meterai", "sinonim": ["bea meterai", "materai", "meterai 10000"], "kategori": "istilah"},
    {"istilah": "cukai", "sinonim": ["cukai rokok", "bea cukai", "cukai barang"], "kategori": "istilah"},
    {"istilah": "NPWP cabang", "sinonim": ["npwp cabang", "pusat cabang", "cabang"], "kategori": "istilah"},
    {"istilah": "insentif pajak", "sinonim": ["tax holiday", "libur pajak", "fasilitas insentif"], "kategori": "istilah"},
]


def seed_default(conn):
    """Isi entri bawaan — mode MERGE idempoten.

    Hanya menambah entri yang 'istilah'-nya belum ada (case-insensitive);
    entri yang sudah ada (termasuk hasil suntingan admin) TIDAK diubah.
    Catatan: entri bawaan yang dihapus akan ditambahkan ulang — nonaktifkan
    (aktif=0) alih-alih menghapus bila ingin mematikan entri bawaan.
    """
    try:
        rows = conn.execute("SELECT istilah FROM kamus_sinonim").fetchall()
        ada = {str(r[0]).strip().lower() for r in rows}
    except Exception:
        return
    n_add = 0
    for row in _DEFAULT_SEED:
        key = str(row.get("istilah") or "").strip().lower()
        if not key or key in ada:
            continue
        try:
            upsert(row, conn=conn)
            ada.add(key)
            n_add += 1
        except Exception:
            pass
    if n_add:
        try:
            print("[rag_kamus_db] seed merge: +%d entri bawaan" % n_add, flush=True)
        except Exception:
            pass
