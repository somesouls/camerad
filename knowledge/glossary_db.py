# -*- coding: utf-8 -*-
"""
glossary_db.py
--------------
Lapisan penyimpanan (SQLite) untuk **Glosarium Istilah Perpajakan**.

Dipakai untuk memberi konteks istilah (EFIN, Coretax, BPPU, dll) saat analisis
fallback & MKTA, sekaligus dikelola manual oleh tim lewat halaman /glossary
(tambah / edit / hapus).

Disimpan di database yang sama dengan analytics (PIPELINE_DB_FILE) pada tabel
`glossary`. Hanya memakai stdlib. Semua field daftar (aliases, dll) disimpan
sebagai TEXT JSON array.
"""
import json
import re
import datetime as _dt

import db.analytics_db as adb  # pakai ulang connect() / default_db_path()

# Nilai enum yang dianjurkan (dipakai UI untuk dropdown; tidak dipaksa di DB).
KATEGORI = [
    "Pelaporan", "Pembayaran", "Autentikasi", "Identitas",
    "Faktur", "Pemotongan/Pemungutan", "Restitusi/Kompensasi",
    "Layanan/Kanal", "Coretax", "Umum",
]
SISTEM = ["umum", "coretax", "djp_online", "e_nofa", "efaktur"]
STATUS = ["aktif", "usang"]


def connect(db_path=None):
    return adb.connect(db_path)


def _now():
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS glossary (
            id                   TEXT PRIMARY KEY,
            term                 TEXT NOT NULL,
            nama_panjang         TEXT,
            aliases              TEXT,   -- JSON array
            kategori             TEXT,
            sistem               TEXT,   -- umum/coretax/djp_online/...
            status               TEXT,   -- aktif/usang
            definisi             TEXT,
            masalah_umum         TEXT,   -- JSON array
            contoh_pertanyaan    TEXT,   -- JSON array
            istilah_terkait      TEXT,   -- JSON array
            prioritas            INTEGER DEFAULT 0,
            sumber               TEXT,
            terverifikasi        INTEGER DEFAULT 0,
            terakhir_diperbarui  TEXT,
            created_at           TEXT,
            updated_at           TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_gl_term     ON glossary(term);
        CREATE INDEX IF NOT EXISTS idx_gl_kategori ON glossary(kategori);
        CREATE INDEX IF NOT EXISTS idx_gl_sistem   ON glossary(sistem);
        """
    )
    _cols = [r[1] for r in conn.execute("PRAGMA table_info(glossary)").fetchall()]
    if "lang" not in _cols:
        conn.execute("ALTER TABLE glossary ADD COLUMN lang TEXT DEFAULT 'id'")
    conn.commit()
    return conn


def _slug(s):
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s or "istilah"


def _norm_lang(v):
    s = str(v or 'id').strip().lower()
    return 'en' if s in ('en', 'eng', 'english', 'inggris') else 'id'




def _norm_list(v):
    """Terima list ATAU string (dipisah baris/koma) -> list string bersih."""
    if v is None:
        return []
    if isinstance(v, str):
        parts = re.split(r"[\n,]", v)
    elif isinstance(v, (list, tuple)):
        parts = []
        for x in v:
            parts.extend(re.split(r"[\n,]", str(x)))
    else:
        parts = [str(v)]
    out, seen = [], set()
    for p in parts:
        p = p.strip()
        k = p.lower()
        if p and k not in seen:
            seen.add(k)
            out.append(p)
    return out


def _to_int(v, default=0):
    try:
        return int(v)
    except Exception:
        try:
            return int(float(v))
        except Exception:
            return default


def _bool01(v):
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, (int, float)):
        return 1 if int(v) != 0 else 0
    s = str(v).strip().lower()
    return 1 if s in ("1", "true", "ya", "yes", "on", "verified") else 0


def _loads(s):
    if not s:
        return []
    try:
        v = json.loads(s)
        return v if isinstance(v, list) else []
    except Exception:
        return []


def row_to_dict(r):
    d = dict(r)
    for k in ("aliases", "masalah_umum", "contoh_pertanyaan", "istilah_terkait"):
        d[k] = _loads(d.get(k))
    d["terverifikasi"] = int(d.get("terverifikasi") or 0)
    d["prioritas"] = int(d.get("prioritas") or 0)
    d["lang"] = (d.get("lang") or "id")
    return d


def validate(data):
    """Kembalikan (ok, pesan). Aturan minimum agar data tidak salah."""
    term = (data.get("term") or "").strip()
    definisi = (data.get("definisi") or "").strip()
    if not term:
        return False, "Field 'term' (istilah) wajib diisi."
    if len(term) > 120:
        return False, "Istilah terlalu panjang (maks 120 karakter)."
    if not definisi:
        return False, "Field 'definisi' wajib diisi (jangan biarkan kosong / dikarang)."
    if len(definisi) < 10:
        return False, "Definisi terlalu pendek; tulis 1-3 kalimat yang jelas."
    sistem = (data.get("sistem") or "umum").strip().lower()
    if sistem not in SISTEM:
        return False, "Sistem tidak valid. Pilih salah satu: " + ", ".join(SISTEM)
    status = (data.get("status") or "aktif").strip().lower()
    if status not in STATUS:
        return False, "Status harus 'aktif' atau 'usang'."
    return True, ""


def upsert_term(conn, data):
    ok, msg = validate(data)
    if not ok:
        raise ValueError(msg)
    term = data["term"].strip()
    gid = (data.get("id") or "").strip() or ("term_" + _slug(term))
    now = _now()
    exists = conn.execute("SELECT created_at FROM glossary WHERE id=?", (gid,)).fetchone()
    created = (exists["created_at"] if exists else now)
    row = (
        gid,
        term,
        (data.get("nama_panjang") or "").strip(),
        json.dumps(_norm_list(data.get("aliases")), ensure_ascii=False),
        (data.get("kategori") or "").strip(),
        (data.get("sistem") or "umum").strip().lower(),
        (data.get("status") or "aktif").strip().lower(),
        (data.get("definisi") or "").strip(),
        json.dumps(_norm_list(data.get("masalah_umum")), ensure_ascii=False),
        json.dumps(_norm_list(data.get("contoh_pertanyaan")), ensure_ascii=False),
        json.dumps(_norm_list(data.get("istilah_terkait")), ensure_ascii=False),
        _to_int(data.get("prioritas"), 0),
        (data.get("sumber") or "").strip(),
        _bool01(data.get("terverifikasi")),
        now[:10],
        created,
        now,
        _norm_lang(data.get("lang")),
    )
    conn.execute(
        """
        INSERT INTO glossary
          (id, term, nama_panjang, aliases, kategori, sistem, status, definisi,
           masalah_umum, contoh_pertanyaan, istilah_terkait, prioritas, sumber,
           terverifikasi, terakhir_diperbarui, created_at, updated_at, lang)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
           term=excluded.term, nama_panjang=excluded.nama_panjang,
           aliases=excluded.aliases, kategori=excluded.kategori,
           sistem=excluded.sistem, status=excluded.status,
           definisi=excluded.definisi, masalah_umum=excluded.masalah_umum,
           contoh_pertanyaan=excluded.contoh_pertanyaan,
           istilah_terkait=excluded.istilah_terkait, prioritas=excluded.prioritas,
           sumber=excluded.sumber, terverifikasi=excluded.terverifikasi,
           terakhir_diperbarui=excluded.terakhir_diperbarui,
           updated_at=excluded.updated_at, lang=excluded.lang
        """,
        row,
    )
    conn.commit()
    return {"ok": True, "id": gid, "created": not bool(exists)}


def get_term(conn, gid):
    r = conn.execute("SELECT * FROM glossary WHERE id=?", (gid,)).fetchone()
    return row_to_dict(r) if r else None


def delete_term(conn, gid):
    cur = conn.execute("DELETE FROM glossary WHERE id=?", (gid,))
    conn.commit()
    return cur.rowcount > 0


def count(conn):
    return conn.execute("SELECT COUNT(*) FROM glossary").fetchone()[0]


def list_terms(conn, q=None, kategori=None, sistem=None, status=None, limit=2000, lang=None):
    sql = "SELECT * FROM glossary"
    where, params = [], []
    if q:
        where.append("(LOWER(term) LIKE ? OR LOWER(aliases) LIKE ? OR LOWER(definisi) LIKE ?)")
        like = "%" + q.strip().lower() + "%"
        params += [like, like, like]
    if kategori:
        where.append("kategori=?"); params.append(kategori)
    if sistem:
        where.append("sistem=?"); params.append(sistem.lower())
    if status:
        where.append("status=?"); params.append(status.lower())
    if lang:
        where.append("lang=?"); params.append(str(lang).lower())
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY prioritas DESC, term COLLATE NOCASE ASC LIMIT ?"
    params.append(_to_int(limit, 2000))
    rows = conn.execute(sql, params).fetchall()
    return [row_to_dict(r) for r in rows]


# --- Pencocokan untuk mesin analisis -------------------------------------
_STOP = set(
    "saya aku kami mau ingin gimana bagaimana cara kok ya dan atau di ke dari "
    "yang untuk apa apakah tolong min pak bu nya dong sih itu ini dulu lama sudah "
    "tidak gak ga tak lagi ada mohon bisa".split()
)


def _tokens(s):
    return [t for t in re.findall(r"[a-z0-9]+", (s or "").lower())
            if t not in _STOP and len(t) > 1]


def _key_hit(key, ql, qtok):
    """True bila 'key' (istilah/alias/contoh) cocok dengan query: substring, atau
    >=60% token penting muncul (>=2 hit); istilah 1 token harus persis ada."""
    key = (key or "").strip().lower()
    if not key:
        return False
    if key in ql:
        return True
    ktok = _tokens(key)
    if not ktok:
        return False
    hit = sum(1 for t in ktok if t in qtok)
    if len(ktok) == 1:
        return hit == 1
    return (hit / len(ktok)) >= 0.6 and hit >= 2


def match(conn, query, limit=5):
    """Cari istilah glosarium yang muncul (persis/mirip) pada query.
    Dipakai mesin analisis untuk menyuntik definisi istilah pajak ke prompt."""
    ql = (query or "").lower()
    if not ql.strip():
        return []
    qtok = set(_tokens(query))
    rows = conn.execute(
        "SELECT * FROM glossary WHERE status='aktif' ORDER BY prioritas DESC"
    ).fetchall()
    hasil = []
    for r in rows:
        d = row_to_dict(r)
        keys = [d.get("term", ""), d.get("nama_panjang", "")]
        keys += list(d.get("aliases") or [])
        keys += list(d.get("contoh_pertanyaan") or [])
        if not any(_key_hit(k, ql, qtok) for k in keys):
            continue
        hasil.append(d)
        if len(hasil) >= limit:
            break
    return hasil


def build_context_text(matches, max_items=6):
    """Ubah hasil match() menjadi teks ringkas untuk disuntik ke system prompt."""
    if not matches:
        return ""
    lines = ["GLOSARIUM ISTILAH PAJAK (definisi acuan \u2014 pakai untuk memahami maksud user):"]
    for m in matches[:max_items]:
        nm = m.get("term", "")
        if m.get("nama_panjang"):
            nm += " (%s)" % m["nama_panjang"]
        lines.append("- %s: %s" % (nm, m.get("definisi", "")))
        mu = m.get("masalah_umum") or []
        if mu:
            lines.append("  Masalah umum: " + "; ".join(mu[:3]))
    return "\n".join(lines)


# --- Data contoh (seed) --------------------------------------------------
# Catatan: istilah GABUNGAN yang punya makna/masalah tersendiri ditulis UTUH
# sebagai entri sendiri (mis. "SPT Masa", "SPT Tahunan"), dengan induk "SPT"
# dicantumkan di istilah_terkait. Istilah baru/khusus Coretax yang belum pasti
# sengaja diberi terverifikasi=0 agar dikoreksi tim dulu.
SEED = [
    {"term": "SPT", "nama_panjang": "Surat Pemberitahuan", "kategori": "Pelaporan",
     "sistem": "umum", "terverifikasi": 1, "prioritas": 90, "sumber": "DJP",
     "aliases": ["spt", "surat pemberitahuan", "lapor spt", "pelaporan pajak"],
     "definisi": "Surat yang digunakan wajib pajak untuk melaporkan penghitungan dan/atau pembayaran pajak, objek/bukan objek pajak, harta, dan kewajiban. Terdiri atas SPT Masa dan SPT Tahunan.",
     "masalah_umum": ["gagal submit spt", "cara lapor spt", "status spt belum diterima"],
     "contoh_pertanyaan": ["bagaimana cara lapor SPT?"], "istilah_terkait": ["SPT Masa", "SPT Tahunan", "EFIN", "e-Filing"]},
    {"term": "SPT Masa", "nama_panjang": "Surat Pemberitahuan Masa", "kategori": "Pelaporan",
     "sistem": "umum", "terverifikasi": 1, "prioritas": 80, "sumber": "DJP",
     "aliases": ["spt masa", "spt bulanan", "lapor masa"],
     "definisi": "SPT untuk melaporkan pajak dalam suatu Masa Pajak (umumnya bulanan), misalnya SPT Masa PPN atau SPT Masa PPh Pasal 21.",
     "masalah_umum": ["telat lapor spt masa", "cara lapor spt masa ppn"],
     "contoh_pertanyaan": ["kapan batas lapor SPT Masa PPN?"], "istilah_terkait": ["SPT", "Masa Pajak", "PPN"]},
    {"term": "SPT Tahunan", "nama_panjang": "Surat Pemberitahuan Tahunan", "kategori": "Pelaporan",
     "sistem": "umum", "terverifikasi": 1, "prioritas": 85, "sumber": "DJP",
     "aliases": ["spt tahunan", "spt tahunan pribadi", "spt tahunan badan", "lapor tahunan"],
     "definisi": "SPT untuk melaporkan pajak dalam satu Tahun Pajak, misalnya SPT Tahunan PPh Orang Pribadi (1770/1770S/1770SS) atau Badan (1771).",
     "masalah_umum": ["cara lapor spt tahunan", "lupa efin saat lapor tahunan", "pembetulan spt tahunan"],
     "contoh_pertanyaan": ["bagaimana lapor SPT Tahunan orang pribadi?"], "istilah_terkait": ["SPT", "Tahun Pajak", "EFIN", "PTKP"]},
    {"term": "NPWP", "nama_panjang": "Nomor Pokok Wajib Pajak", "kategori": "Identitas",
     "sistem": "umum", "terverifikasi": 1, "prioritas": 90, "sumber": "DJP",
     "aliases": ["npwp", "nomor pokok wajib pajak", "kartu npwp", "npwp 16 digit"],
     "definisi": "Nomor identitas wajib pajak sebagai sarana administrasi perpajakan. NPWP orang pribadi kini menggunakan format 16 digit yang terintegrasi dengan NIK.",
     "masalah_umum": ["cara daftar npwp", "lupa npwp", "npwp non-efektif", "padankan nik dan npwp"],
     "contoh_pertanyaan": ["bagaimana cara daftar NPWP online?"], "istilah_terkait": ["NIK", "EFIN", "Coretax"]},
    {"term": "NIK", "nama_panjang": "Nomor Induk Kependudukan", "kategori": "Identitas",
     "sistem": "umum", "terverifikasi": 1, "prioritas": 60, "sumber": "DJP",
     "aliases": ["nik", "nik sebagai npwp", "padankan nik", "validasi nik"],
     "definisi": "Nomor Induk Kependudukan yang sejak integrasi menjadi identitas perpajakan orang pribadi (NIK sebagai NPWP 16 digit).",
     "masalah_umum": ["nik belum valid", "gagal padankan nik npwp"],
     "contoh_pertanyaan": ["kenapa NIK saya belum bisa dipakai sebagai NPWP?"], "istilah_terkait": ["NPWP", "Coretax"]},
    {"term": "EFIN", "nama_panjang": "Electronic Filing Identification Number", "kategori": "Autentikasi",
     "sistem": "umum", "terverifikasi": 1, "prioritas": 88, "sumber": "DJP",
     "aliases": ["efin", "e-fin", "nomor efin", "aktivasi efin", "lupa efin", "reset efin"],
     "definisi": "Nomor identitas yang diterbitkan DJP agar wajib pajak dapat melakukan transaksi elektronik (mis. aktivasi akun DJP Online dan e-Filing).",
     "masalah_umum": ["lupa efin", "cara aktivasi efin", "efin belum aktif", "minta efin tanpa ke kpp"],
     "contoh_pertanyaan": ["bagaimana cara reset EFIN yang lupa?"], "istilah_terkait": ["DJP Online", "e-Filing", "NPWP"]},
    {"term": "PKP", "nama_panjang": "Pengusaha Kena Pajak", "kategori": "Identitas",
     "sistem": "umum", "terverifikasi": 1, "prioritas": 55, "sumber": "DJP",
     "aliases": ["pkp", "pengusaha kena pajak", "pengukuhan pkp"],
     "definisi": "Pengusaha yang melakukan penyerahan Barang/Jasa Kena Pajak yang dikenai PPN dan wajib dikukuhkan sebagai PKP; berhak menerbitkan faktur pajak.",
     "masalah_umum": ["cara jadi pkp", "syarat pengukuhan pkp"],
     "contoh_pertanyaan": ["apa syarat menjadi PKP?"], "istilah_terkait": ["PPN", "Faktur Pajak"]},
    {"term": "PPh", "nama_panjang": "Pajak Penghasilan", "kategori": "Umum",
     "sistem": "umum", "terverifikasi": 1, "prioritas": 70, "sumber": "DJP",
     "aliases": ["pph", "pajak penghasilan"],
     "definisi": "Pajak yang dikenakan atas penghasilan yang diterima atau diperoleh wajib pajak dalam suatu tahun pajak.",
     "masalah_umum": ["cara hitung pph", "tarif pph"],
     "contoh_pertanyaan": ["bagaimana menghitung PPh?"], "istilah_terkait": ["PPh Pasal 21", "PPh Pasal 23", "PPh Final"]},
    {"term": "PPh Pasal 21", "nama_panjang": "Pajak Penghasilan Pasal 21", "kategori": "Pemotongan/Pemungutan",
     "sistem": "umum", "terverifikasi": 1, "prioritas": 65, "sumber": "DJP",
     "aliases": ["pph 21", "pph pasal 21", "pajak gaji"],
     "definisi": "PPh atas penghasilan berupa gaji, upah, honorarium, tunjangan, dan pembayaran lain sehubungan dengan pekerjaan/jabatan, jasa, dan kegiatan orang pribadi.",
     "masalah_umum": ["cara hitung pph 21 karyawan", "ter pph 21"],
     "contoh_pertanyaan": ["berapa tarif PPh 21 dengan skema TER?"], "istilah_terkait": ["PPh", "Bukti Potong", "PTKP"]},
    {"term": "PPh Pasal 23", "nama_panjang": "Pajak Penghasilan Pasal 23", "kategori": "Pemotongan/Pemungutan",
     "sistem": "umum", "terverifikasi": 1, "prioritas": 50, "sumber": "DJP",
     "aliases": ["pph 23", "pph pasal 23"],
     "definisi": "PPh atas penghasilan berupa dividen, bunga, royalti, sewa, dan imbalan jasa tertentu yang dipotong oleh pihak pemberi penghasilan.",
     "masalah_umum": ["tarif pph 23 jasa", "bukti potong pph 23"],
     "contoh_pertanyaan": ["berapa tarif PPh 23 atas jasa?"], "istilah_terkait": ["PPh", "Bukti Potong"]},
    {"term": "PPh Final", "nama_panjang": "Pajak Penghasilan Bersifat Final", "kategori": "Umum",
     "sistem": "umum", "terverifikasi": 1, "prioritas": 45, "sumber": "DJP",
     "aliases": ["pph final", "pph final umkm", "pph 0.5%", "pp 55"],
     "definisi": "PPh yang pengenaannya bersifat final sehingga tidak diperhitungkan lagi dengan PPh terutang lainnya, mis. PPh final UMKM 0,5% dari peredaran bruto.",
     "masalah_umum": ["cara bayar pph final umkm", "batas omzet pph final"],
     "contoh_pertanyaan": ["bagaimana bayar PPh final UMKM 0,5%?"], "istilah_terkait": ["PPh", "Kode Billing"]},
    {"term": "PPN", "nama_panjang": "Pajak Pertambahan Nilai", "kategori": "Umum",
     "sistem": "umum", "terverifikasi": 1, "prioritas": 70, "sumber": "DJP",
     "aliases": ["ppn", "pajak pertambahan nilai", "pajak ppn"],
     "definisi": "Pajak atas konsumsi Barang/Jasa Kena Pajak di dalam Daerah Pabean yang dipungut oleh Pengusaha Kena Pajak.",
     "masalah_umum": ["tarif ppn", "cara lapor ppn", "faktur ppn"],
     "contoh_pertanyaan": ["berapa tarif PPN saat ini?"], "istilah_terkait": ["PKP", "Faktur Pajak", "Nota Retur"]},
    {"term": "PPnBM", "nama_panjang": "Pajak Penjualan atas Barang Mewah", "kategori": "Umum",
     "sistem": "umum", "terverifikasi": 1, "prioritas": 30, "sumber": "DJP",
     "aliases": ["ppnbm", "pajak barang mewah"],
     "definisi": "Pajak yang dikenakan atas penyerahan/impor Barang Kena Pajak yang tergolong mewah, di samping PPN.",
     "masalah_umum": ["tarif ppnbm mobil"],
     "contoh_pertanyaan": ["apa itu PPnBM?"], "istilah_terkait": ["PPN"]},
    {"term": "Faktur Pajak", "nama_panjang": "", "kategori": "Faktur",
     "sistem": "umum", "terverifikasi": 1, "prioritas": 60, "sumber": "DJP",
     "aliases": ["faktur pajak", "fp", "nomor seri faktur pajak", "nsfp"],
     "definisi": "Bukti pungutan pajak yang dibuat Pengusaha Kena Pajak atas penyerahan Barang/Jasa Kena Pajak atau bukti pungutan PPN impor.",
     "masalah_umum": ["cara buat faktur pajak", "faktur pajak batal", "nomor seri faktur habis"],
     "contoh_pertanyaan": ["bagaimana membuat faktur pajak keluaran?"], "istilah_terkait": ["e-Faktur", "PPN", "PKP", "Nota Retur"]},
    {"term": "e-Faktur", "nama_panjang": "Aplikasi Faktur Pajak Elektronik", "kategori": "Faktur",
     "sistem": "efaktur", "terverifikasi": 1, "prioritas": 55, "sumber": "DJP",
     "aliases": ["e-faktur", "efaktur", "aplikasi e-faktur"],
     "definisi": "Aplikasi resmi DJP untuk membuat, mengunggah, dan mengelola faktur pajak elektronik.",
     "masalah_umum": ["efaktur error", "upload faktur gagal", "sertifikat efaktur kedaluwarsa"],
     "contoh_pertanyaan": ["kenapa e-Faktur gagal upload?"], "istilah_terkait": ["Faktur Pajak", "e-Nofa"]},
    {"term": "e-Nofa", "nama_panjang": "Elektronik Nomor Faktur", "kategori": "Faktur",
     "sistem": "e_nofa", "terverifikasi": 1, "prioritas": 35, "sumber": "DJP",
     "aliases": ["e-nofa", "enofa", "minta nomor seri faktur"],
     "definisi": "Layanan permintaan Nomor Seri Faktur Pajak (NSFP) secara elektronik.",
     "masalah_umum": ["cara minta nomor seri faktur di enofa"],
     "contoh_pertanyaan": ["bagaimana minta NSFP lewat e-Nofa?"], "istilah_terkait": ["Faktur Pajak", "e-Faktur"]},
    {"term": "e-Filing", "nama_panjang": "", "kategori": "Pelaporan",
     "sistem": "djp_online", "terverifikasi": 1, "prioritas": 60, "sumber": "DJP",
     "aliases": ["e-filing", "efiling", "lapor online"],
     "definisi": "Layanan pelaporan SPT secara online melalui DJP Online atau penyedia layanan (PJAP).",
     "masalah_umum": ["efiling error", "gagal submit efiling", "butuh efin untuk efiling"],
     "contoh_pertanyaan": ["bagaimana lapor SPT lewat e-Filing?"], "istilah_terkait": ["DJP Online", "EFIN", "SPT"]},
    {"term": "e-Billing", "nama_panjang": "", "kategori": "Pembayaran",
     "sistem": "djp_online", "terverifikasi": 1, "prioritas": 55, "sumber": "DJP",
     "aliases": ["e-billing", "ebilling", "buat kode billing"],
     "definisi": "Sistem pembuatan Kode Billing untuk pembayaran pajak secara elektronik.",
     "masalah_umum": ["cara buat kode billing", "kode billing kedaluwarsa"],
     "contoh_pertanyaan": ["bagaimana membuat kode billing?"], "istilah_terkait": ["Kode Billing", "NTPN"]},
    {"term": "Kode Billing", "nama_panjang": "", "kategori": "Pembayaran",
     "sistem": "umum", "terverifikasi": 1, "prioritas": 58, "sumber": "DJP",
     "aliases": ["kode billing", "id billing", "billing pajak"],
     "definisi": "Kode identifikasi yang diterbitkan sistem billing untuk membayar sejumlah pajak tertentu melalui bank/kanal pembayaran.",
     "masalah_umum": ["kode billing expired", "salah kode billing"],
     "contoh_pertanyaan": ["berapa lama kode billing berlaku?"], "istilah_terkait": ["e-Billing", "NTPN", "SSP"]},
    {"term": "NTPN", "nama_panjang": "Nomor Transaksi Penerimaan Negara", "kategori": "Pembayaran",
     "sistem": "umum", "terverifikasi": 1, "prioritas": 40, "sumber": "DJP",
     "aliases": ["ntpn", "nomor transaksi penerimaan negara", "bukti bayar ntpn"],
     "definisi": "Nomor bukti penyetoran pajak yang diterbitkan sistem penerimaan negara sebagai tanda pembayaran telah diterima kas negara.",
     "masalah_umum": ["ntpn tidak muncul", "cara cek ntpn"],
     "contoh_pertanyaan": ["di mana melihat NTPN setelah bayar?"], "istilah_terkait": ["Kode Billing", "SSP"]},
    {"term": "SSP", "nama_panjang": "Surat Setoran Pajak", "kategori": "Pembayaran",
     "sistem": "umum", "terverifikasi": 1, "prioritas": 30, "sumber": "DJP",
     "aliases": ["ssp", "surat setoran pajak"],
     "definisi": "Bukti pembayaran atau penyetoran pajak yang telah dilakukan dengan menggunakan formulir/sarana administrasi lain ke kas negara.",
     "masalah_umum": ["beda ssp dan kode billing"],
     "contoh_pertanyaan": ["apa itu SSP?"], "istilah_terkait": ["Kode Billing", "NTPN"]},
    {"term": "DJP Online", "nama_panjang": "", "kategori": "Layanan/Kanal",
     "sistem": "djp_online", "terverifikasi": 1, "prioritas": 65, "sumber": "DJP",
     "aliases": ["djp online", "djponline", "akun djp", "login pajak"],
     "definisi": "Portal layanan pajak online DJP untuk e-Filing, e-Billing, dan layanan lainnya.",
     "masalah_umum": ["tidak bisa login djp online", "lupa password djp online", "aktivasi akun djp"],
     "contoh_pertanyaan": ["kenapa tidak bisa login DJP Online?"], "istilah_terkait": ["EFIN", "e-Filing", "Coretax"]},
    {"term": "PTKP", "nama_panjang": "Penghasilan Tidak Kena Pajak", "kategori": "Umum",
     "sistem": "umum", "terverifikasi": 1, "prioritas": 35, "sumber": "DJP",
     "aliases": ["ptkp", "penghasilan tidak kena pajak"],
     "definisi": "Batas penghasilan yang tidak dikenai PPh Orang Pribadi, besarannya bergantung status kawin dan jumlah tanggungan.",
     "masalah_umum": ["besaran ptkp", "ptkp k/1"],
     "contoh_pertanyaan": ["berapa PTKP untuk status K/1?"], "istilah_terkait": ["PPh Pasal 21", "SPT Tahunan"]},
    {"term": "Bukti Potong", "nama_panjang": "Bukti Pemotongan/Pemungutan", "kategori": "Pemotongan/Pemungutan",
     "sistem": "umum", "terverifikasi": 1, "prioritas": 50, "sumber": "DJP",
     "aliases": ["bukti potong", "bupot", "bukti pemotongan", "e-bupot"],
     "definisi": "Dokumen yang dibuat pemotong/pemungut sebagai bukti atas pemotongan/pemungutan PPh, mis. Bukti Potong PPh 21/23.",
     "masalah_umum": ["cara buat bukti potong", "e-bupot error", "bukti potong hilang"],
     "contoh_pertanyaan": ["bagaimana membuat bukti potong PPh 23?"], "istilah_terkait": ["PPh Pasal 21", "PPh Pasal 23", "BPPU"]},
    {"term": "Nota Retur", "nama_panjang": "", "kategori": "Faktur",
     "sistem": "umum", "terverifikasi": 1, "prioritas": 40, "sumber": "DJP",
     "aliases": ["nota retur", "retur pajak", "retur ppn", "nota retur ppn"],
     "definisi": "Dokumen yang dibuat pembeli saat mengembalikan Barang Kena Pajak, yang mengurangi PPN (dan PPnBM) yang telah dilaporkan penjual.",
     "masalah_umum": ["cara buat nota retur", "nota retur di efaktur"],
     "contoh_pertanyaan": ["bagaimana membuat nota retur di e-Faktur?"], "istilah_terkait": ["Faktur Pajak", "PPN", "e-Faktur"]},
    {"term": "Restitusi", "nama_panjang": "", "kategori": "Restitusi/Kompensasi",
     "sistem": "umum", "terverifikasi": 1, "prioritas": 40, "sumber": "DJP",
     "aliases": ["restitusi", "restitusi pajak", "pengembalian pajak", "lebih bayar"],
     "definisi": "Pengembalian kelebihan pembayaran pajak kepada wajib pajak setelah melalui pemeriksaan/penelitian.",
     "masalah_umum": ["cara ajukan restitusi", "restitusi ppn lebih bayar"],
     "contoh_pertanyaan": ["bagaimana mengajukan restitusi PPN?"], "istilah_terkait": ["PPN", "SPT"]},
    {"term": "KPP", "nama_panjang": "Kantor Pelayanan Pajak", "kategori": "Layanan/Kanal",
     "sistem": "umum", "terverifikasi": 1, "prioritas": 30, "sumber": "DJP",
     "aliases": ["kpp", "kantor pelayanan pajak", "kantor pajak"],
     "definisi": "Unit kerja DJP yang memberikan pelayanan perpajakan kepada wajib pajak di wilayah tertentu.",
     "masalah_umum": ["alamat kpp terdaftar", "pindah kpp"],
     "contoh_pertanyaan": ["bagaimana pindah KPP?"], "istilah_terkait": ["NPWP"]},
    {"term": "Coretax", "nama_panjang": "Coretax Administration System (Sistem Inti Administrasi Perpajakan)", "kategori": "Coretax",
     "sistem": "coretax", "terverifikasi": 0, "prioritas": 95, "sumber": "perlu verifikasi",
     "aliases": ["coretax", "core tax", " core tax djp", "sistem coretax", "pbk coretax"],
     "definisi": "[PERLU VERIFIKASI TIM] Sistem inti administrasi perpajakan DJP generasi baru yang mengintegrasikan pendaftaran, pembayaran, pelaporan, dan layanan lain dalam satu portal. Mohon lengkapi definisi resmi.",
     "masalah_umum": ["tidak bisa login coretax", "menu coretax membingungkan", "error saat migrasi ke coretax"],
     "contoh_pertanyaan": ["bagaimana cara login ke Coretax?"], "istilah_terkait": ["Kode Otorisasi DJP", "BPPU", "DJP Online"]},
    {"term": "Kode Otorisasi DJP", "nama_panjang": "", "kategori": "Coretax",
     "sistem": "coretax", "terverifikasi": 0, "prioritas": 92, "sumber": "perlu verifikasi",
     "aliases": ["kode otorisasi djp", "kode otorisasi", "otorisasi coretax"],
     "definisi": "[PERLU VERIFIKASI TIM] Kode/mekanisme otorisasi di Coretax yang digunakan untuk menandatangani atau mengesahkan dokumen/transaksi secara elektronik (pengganti sertifikat elektronik/passphrase). Mohon lengkapi definisi resmi.",
     "masalah_umum": ["cara buat kode otorisasi djp", "lupa kode otorisasi", "kode otorisasi tidak berfungsi"],
     "contoh_pertanyaan": ["bagaimana membuat Kode Otorisasi DJP di Coretax?"], "istilah_terkait": ["Coretax"]},
    {"term": "BPPU", "nama_panjang": "Bukti Pemotongan/Pemungutan Unifikasi (perlu verifikasi)", "kategori": "Coretax",
     "sistem": "coretax", "terverifikasi": 0, "prioritas": 90, "sumber": "perlu verifikasi",
     "aliases": ["bppu", "bukti potong unifikasi", "bupot unifikasi"],
     "definisi": "[PERLU VERIFIKASI TIM] Diduga singkatan Bukti Pemotongan/Pemungutan Unifikasi pada Coretax. Mohon konfirmasi kepanjangan & definisi resmi sebelum dipakai analisis.",
     "masalah_umum": ["cara buat bppu di coretax", "bppu tidak bisa dibuat"],
     "contoh_pertanyaan": ["bagaimana membuat BPPU di Coretax?"], "istilah_terkait": ["Coretax", "Bukti Potong"]},
]


def seed_defaults(conn, force=False):
    """Isi contoh istilah bila glossary masih kosong (atau force=True)."""
    if not force and count(conn) > 0:
        return 0
    n = 0
    for item in SEED:
        upsert_term(conn, item)
        n += 1
    return n


if __name__ == "__main__":
    c = init_db(connect())
    added = seed_defaults(c, force=False)
    print("glossary rows:", count(c), "(seeded:", added, ")")
    sample = list_terms(c, q="spt")
    print("contoh cari 'spt':", [x["term"] for x in sample])
    c.close()
