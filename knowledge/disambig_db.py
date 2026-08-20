# -*- coding: utf-8 -*-
"""
disambig_db.py
--------------
Lapisan penyimpanan (SQLite) untuk **Pustaka Disambiguasi & Routing**.

Berbeda dengan glosarium (kamus: 1 istilah -> 1 definisi), pustaka ini menangani
frasa user yang AMBIGU / bercabang, misalnya:
  - "lupa password"  -> DJP Online? atau Coretax?
  - "minta efin"     -> lupa EFIN? atau aktivasi EFIN?
  - "aktivasi"       -> aktivasi NIK / NPWP / EFIN / akun

Setiap entri berisi:
  - pemicu           : frasa utama yang memicu ambiguitas
  - pola             : variasi/kata kunci lain yang juga memicu (JSON array)
  - kandidat         : daftar cabang kemungkinan (JSON array of objek)
                       {label, sistem, intent_bot, petunjuk}
  - aturan_temporal  : aturan pemilihan sistem berbasis MASA PAJAK yang
                       ditanyakan user (JSON objek) {aktif, cutoff, sebelum, sejak}
  - pertanyaan_klarifikasi : pertanyaan yang sebaiknya diajukan bot

Aturan temporal khusus DJP: Coretax mulai dipakai untuk MASA PAJAK Januari 2025
dan sesudahnya. Maka untuk frasa seperti pelaporan/pembayaran/login, penentu
adalah MASA PAJAK yang ditanyakan user (BUKAN tanggal user bertanya): masa pajak
>= cutoff -> Coretax, sebelum itu -> DJP Online. Aturan ini DISAJIKAN ke LLM
untuk diterapkan berdasarkan masa pajak di teks pertanyaan; jika masa pajak tak
disebut, tandai AMBIGU.

Disimpan di database yang sama (PIPELINE_DB_FILE) pada tabel `disambig`.
Hanya memakai stdlib.
"""
import json
import re
import datetime as _dt

import analytics_db as adb  # pakai ulang connect() / default_db_path()

# Nilai enum yang dianjurkan (dipakai UI untuk dropdown; tidak dipaksa di DB).
KATEGORI = [
    "Autentikasi", "Aktivasi", "Pelaporan", "Pembayaran",
    "Identitas", "Faktur", "Layanan/Kanal", "Umum",
]
# Produk/sistem tujuan cabang. "lintas" = tidak terikat satu sistem.
SISTEM = ["lintas", "umum", "coretax", "djp_online", "e_nofa", "efaktur"]
STATUS = ["aktif", "usang"]

# Cutoff default peralihan DJP Online -> Coretax (bisa diganti per-entri).
DEFAULT_CUTOFF = "2025-01-01"


def connect(db_path=None):
    return adb.connect(db_path)


def _now():
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS disambig (
            id                    TEXT PRIMARY KEY,
            pemicu                TEXT NOT NULL,
            pola                  TEXT,   -- JSON array (variasi/kata kunci)
            kategori              TEXT,
            kandidat              TEXT,   -- JSON array of {label,sistem,intent_bot,petunjuk}
            aturan_temporal       TEXT,   -- JSON objek {aktif,cutoff,sebelum,sejak}
            pertanyaan_klarifikasi TEXT,
            catatan               TEXT,
            prioritas             INTEGER DEFAULT 0,
            status                TEXT,   -- aktif/usang
            terverifikasi         INTEGER DEFAULT 0,
            terakhir_diperbarui   TEXT,
            created_at            TEXT,
            updated_at            TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_ds_pemicu   ON disambig(pemicu);
        CREATE INDEX IF NOT EXISTS idx_ds_kategori ON disambig(kategori);
        CREATE INDEX IF NOT EXISTS idx_ds_status   ON disambig(status);
        """
    )
    _cols = [r[1] for r in conn.execute("PRAGMA table_info(disambig)").fetchall()]
    if "lang" not in _cols:
        conn.execute("ALTER TABLE disambig ADD COLUMN lang TEXT DEFAULT 'id'")
    conn.commit()
    return conn


def _slug(s):
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s or "aturan"


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


def _norm_kandidat(v):
    """Normalkan daftar kandidat cabang.
    Terima:
      - list objek {label,sistem,intent_bot,petunjuk}
      - list string / string multi-baris "label | sistem | intent | petunjuk"
    """
    items = []
    if v is None:
        return items
    if isinstance(v, str):
        raw = [ln for ln in v.split("\n")]
    elif isinstance(v, (list, tuple)):
        raw = list(v)
    else:
        raw = [v]
    for it in raw:
        label = sistem = intent_bot = petunjuk = ""
        if isinstance(it, dict):
            label = (it.get("label") or "").strip()
            sistem = (it.get("sistem") or it.get("produk") or "").strip().lower()
            intent_bot = (it.get("intent_bot") or it.get("intent") or "").strip()
            petunjuk = (it.get("petunjuk") or it.get("cue") or "").strip()
        else:
            s = str(it).strip()
            if not s:
                continue
            bits = [b.strip() for b in s.split("|")]
            label = bits[0] if len(bits) > 0 else ""
            sistem = (bits[1].lower() if len(bits) > 1 else "")
            intent_bot = bits[2] if len(bits) > 2 else ""
            petunjuk = bits[3] if len(bits) > 3 else ""
        if not label:
            continue
        if sistem and sistem not in SISTEM:
            sistem = "lintas"
        items.append({
            "label": label,
            "sistem": sistem or "lintas",
            "intent_bot": intent_bot,
            "petunjuk": petunjuk,
        })
    return items


def _norm_temporal(v):
    """Normalkan aturan_temporal -> {aktif,cutoff,sebelum,sejak} atau {} bila mati."""
    if not v:
        return {}
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except Exception:
            return {}
    if not isinstance(v, dict):
        return {}
    aktif = _bool01(v.get("aktif")) == 1
    if not aktif:
        return {}
    cutoff = (v.get("cutoff") or DEFAULT_CUTOFF).strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", cutoff):
        cutoff = DEFAULT_CUTOFF
    return {
        "aktif": True,
        "cutoff": cutoff,
        "sebelum": (v.get("sebelum") or "djp_online").strip().lower(),
        "sejak": (v.get("sejak") or "coretax").strip().lower(),
    }


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


def _loads(s, default=None):
    if not s:
        return default if default is not None else []
    try:
        return json.loads(s)
    except Exception:
        return default if default is not None else []


def row_to_dict(r):
    d = dict(r)
    d["pola"] = _loads(d.get("pola"), [])
    d["kandidat"] = _loads(d.get("kandidat"), [])
    d["aturan_temporal"] = _loads(d.get("aturan_temporal"), {}) or {}
    d["terverifikasi"] = int(d.get("terverifikasi") or 0)
    d["prioritas"] = int(d.get("prioritas") or 0)
    d["lang"] = (d.get("lang") or "id")
    return d


def validate(data):
    """Kembalikan (ok, pesan). Aturan minimum agar data tidak salah."""
    pemicu = (data.get("pemicu") or "").strip()
    if not pemicu:
        return False, "Field 'pemicu' (frasa yang memicu) wajib diisi."
    if len(pemicu) > 120:
        return False, "Pemicu terlalu panjang (maks 120 karakter)."
    kandidat = _norm_kandidat(data.get("kandidat"))
    if len(kandidat) < 2:
        return False, "Minimal 2 kandidat cabang (kalau cuma 1 makna, tidak perlu disambiguasi)."
    for k in kandidat:
        if not k.get("label"):
            return False, "Setiap kandidat wajib punya 'label'."
    status = (data.get("status") or "aktif").strip().lower()
    if status not in STATUS:
        return False, "Status harus 'aktif' atau 'usang'."
    tem = _norm_temporal(data.get("aturan_temporal"))
    if tem:
        if tem["sebelum"] not in SISTEM or tem["sejak"] not in SISTEM:
            return False, "Aturan temporal: 'sebelum'/'sejak' harus salah satu sistem valid."
    return True, ""


def upsert_rule(conn, data):
    ok, msg = validate(data)
    if not ok:
        raise ValueError(msg)
    pemicu = data["pemicu"].strip()
    rid = (data.get("id") or "").strip() or ("ds_" + _slug(pemicu))
    now = _now()
    exists = conn.execute("SELECT created_at FROM disambig WHERE id=?", (rid,)).fetchone()
    created = (exists["created_at"] if exists else now)
    row = (
        rid,
        pemicu,
        json.dumps(_norm_list(data.get("pola")), ensure_ascii=False),
        (data.get("kategori") or "").strip(),
        json.dumps(_norm_kandidat(data.get("kandidat")), ensure_ascii=False),
        json.dumps(_norm_temporal(data.get("aturan_temporal")), ensure_ascii=False),
        (data.get("pertanyaan_klarifikasi") or "").strip(),
        (data.get("catatan") or "").strip(),
        _to_int(data.get("prioritas"), 0),
        (data.get("status") or "aktif").strip().lower(),
        _bool01(data.get("terverifikasi")),
        now[:10],
        created,
        now,
        _norm_lang(data.get("lang")),
    )
    conn.execute(
        """
        INSERT INTO disambig
          (id, pemicu, pola, kategori, kandidat, aturan_temporal,
           pertanyaan_klarifikasi, catatan, prioritas, status,
           terverifikasi, terakhir_diperbarui, created_at, updated_at, lang)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
           pemicu=excluded.pemicu, pola=excluded.pola, kategori=excluded.kategori,
           kandidat=excluded.kandidat, aturan_temporal=excluded.aturan_temporal,
           pertanyaan_klarifikasi=excluded.pertanyaan_klarifikasi,
           catatan=excluded.catatan, prioritas=excluded.prioritas,
           status=excluded.status, terverifikasi=excluded.terverifikasi,
           terakhir_diperbarui=excluded.terakhir_diperbarui,
           updated_at=excluded.updated_at, lang=excluded.lang
        """,
        row,
    )
    conn.commit()
    return {"ok": True, "id": rid, "created": not bool(exists)}


def get_rule(conn, rid):
    r = conn.execute("SELECT * FROM disambig WHERE id=?", (rid,)).fetchone()
    return row_to_dict(r) if r else None


def delete_rule(conn, rid):
    cur = conn.execute("DELETE FROM disambig WHERE id=?", (rid,))
    conn.commit()
    return cur.rowcount > 0


def count(conn):
    return conn.execute("SELECT COUNT(*) FROM disambig").fetchone()[0]


def list_rules(conn, q=None, kategori=None, status=None, limit=2000, lang=None):
    sql = "SELECT * FROM disambig"
    where, params = [], []
    if q:
        where.append("(LOWER(pemicu) LIKE ? OR LOWER(pola) LIKE ? OR LOWER(kandidat) LIKE ?)")
        like = "%" + q.strip().lower() + "%"
        params += [like, like, like]
    if kategori:
        where.append("kategori=?"); params.append(kategori)
    if status:
        where.append("status=?"); params.append(status.lower())
    if lang:
        where.append("lang=?"); params.append(str(lang).lower())
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY prioritas DESC, pemicu COLLATE NOCASE ASC LIMIT ?"
    params.append(_to_int(limit, 2000))
    rows = conn.execute(sql, params).fetchall()
    return [row_to_dict(r) for r in rows]


def _resolve_temporal(tem, tanggal):
    """Kembalikan (produk, alasan) berdasarkan aturan temporal & tanggal (YYYY-MM-DD).
    Bila tidak bisa diputus, kembalikan (None, alasan)."""
    if not tem or not tem.get("aktif"):
        return None, ""
    if not tanggal:
        return None, "tanggal log tidak tersedia"
    d = str(tanggal)[:10]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", d):
        return None, "format tanggal tidak dikenali"
    cutoff = tem.get("cutoff", DEFAULT_CUTOFF)
    if d >= cutoff:
        return tem.get("sejak"), "tanggal %s >= %s -> %s" % (d, cutoff, tem.get("sejak"))
    return tem.get("sebelum"), "tanggal %s < %s -> %s" % (d, cutoff, tem.get("sebelum"))


def match(conn, query, tanggal=None, limit=5):
    """Cari aturan disambiguasi yang cocok untuk sebuah query.
    Dipakai mesin analisis untuk membangun konteks (deterministik dulu).
    Mengembalikan list: {id, pemicu, kandidat, pertanyaan_klarifikasi,
    keputusan_temporal:{produk,alasan}|None, kandidat_terpilih|None}.
    """
    ql = (query or "").lower()
    if not ql.strip():
        return []
    rows = conn.execute(
        "SELECT * FROM disambig WHERE status='aktif' ORDER BY prioritas DESC"
    ).fetchall()
    hasil = []
    for r in rows:
        d = row_to_dict(r)
        keys = [d["pemicu"].lower()] + [str(p).lower() for p in (d.get("pola") or [])]
        if not any(k and k in ql for k in keys):
            continue
        produk, alasan = _resolve_temporal(d.get("aturan_temporal") or {}, tanggal)
        terpilih = None
        if produk:
            for k in d.get("kandidat") or []:
                if (k.get("sistem") or "").lower() == produk:
                    terpilih = k
                    break
        hasil.append({
            "id": d["id"],
            "pemicu": d["pemicu"],
            "kategori": d.get("kategori", ""),
            "kandidat": d.get("kandidat") or [],
            "pertanyaan_klarifikasi": d.get("pertanyaan_klarifikasi", ""),
            "aturan_temporal": d.get("aturan_temporal") or {},
            "keputusan_temporal": ({"produk": produk, "alasan": alasan} if produk else None),
            "kandidat_terpilih": terpilih,
        })
        if len(hasil) >= limit:
            break
    return hasil


_SISTEM_LABEL = {
    "djp_online": "DJP Online",
    "coretax": "Coretax",
    "e_nofa": "e-Nofa",
    "efaktur": "e-Faktur",
    "umum": "umum",
    "lintas": "lintas-sistem",
}

_BULAN = ["", "Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli",
          "Agustus", "September", "Oktober", "November", "Desember"]


def _sis_label(s):
    return _SISTEM_LABEL.get((s or "").lower(), s or "")


def _cutoff_label(cutoff):
    """'2025-01-01' -> 'Januari 2025' (untuk penjelasan berbasis masa pajak)."""
    m = re.match(r"^(\d{4})-(\d{2})-\d{2}$", cutoff or "")
    if not m:
        return cutoff or ""
    mo = int(m.group(2))
    return "%s %s" % (_BULAN[mo] if 1 <= mo <= 12 else m.group(2), m.group(1))


def build_context_text(matches, max_rules=6):
    """Ubah hasil match() menjadi teks ringkas untuk disuntik ke system prompt.

    PENTING: untuk ambiguitas antar-sistem (DJP Online vs Coretax), penentu
    adalah MASA PAJAK yang ditanyakan user, BUKAN tanggal user bertanya. Aturan
    hanya DISAJIKAN; penerapannya diserahkan ke LLM yang membaca masa pajak dari
    teks pertanyaan.
    """
    if not matches:
        return ""
    lines = ["PANDUAN DISAMBIGUASI (pakai untuk memilih makna yang benar):"]
    for m in matches[:max_rules]:
        opsi = "; ".join(
            "%s [%s]%s" % (
                k.get("label", ""),
                _sis_label(k.get("sistem", "lintas")),
                (" - " + k.get("petunjuk")) if k.get("petunjuk") else "",
            )
            for k in m.get("kandidat", [])
        )
        lines.append('- Frasa "%s" bersifat ambigu. Kemungkinan: %s' % (m["pemicu"], opsi))
        tem = m.get("aturan_temporal") or {}
        if tem.get("aktif"):
            lines.append(
                "  Penentu = MASA PAJAK yang ditanyakan user (BUKAN tanggal user bertanya): "
                "masa pajak %s ke atas -> %s; sebelum itu -> %s." % (
                    _cutoff_label(tem.get("cutoff", DEFAULT_CUTOFF)),
                    _sis_label(tem.get("sejak")), _sis_label(tem.get("sebelum"))))
            if m.get("pertanyaan_klarifikasi"):
                lines.append("  Jika masa pajak tidak disebut, tandai AMBIGU. Klarifikasi: "
                             + m["pertanyaan_klarifikasi"])
        elif m.get("pertanyaan_klarifikasi"):
            lines.append("  Jika tak pasti, tandai AMBIGU. Saran klarifikasi: " + m["pertanyaan_klarifikasi"])
    return "\n".join(lines)


# --- Data contoh (seed) --------------------------------------------------
SEED = [
    {"pemicu": "lupa password", "kategori": "Autentikasi", "prioritas": 90,
     "terverifikasi": 1, "status": "aktif",
     "pola": ["lupa password", "lupa kata sandi", "reset password", "reset kata sandi", "password salah", "ganti password"],
     "kandidat": [
        {"label": "Lupa password DJP Online", "sistem": "djp_online", "intent_bot": "Reset_Password_DJPOnline", "petunjuk": "masa pajak <= 2024, atau menyebut djponline/e-filing"},
        {"label": "Lupa password Coretax", "sistem": "coretax", "intent_bot": "Reset_Password_Coretax", "petunjuk": "masa pajak >= 2025 atau menyebut coretax/akun baru"},
     ],
     "aturan_temporal": {"aktif": True, "cutoff": "2025-01-01", "sebelum": "djp_online", "sejak": "coretax"},
     "pertanyaan_klarifikasi": "Apakah Bapak/Ibu lupa password akun DJP Online atau akun Coretax?",
     "catatan": "Sebelum 2025 mayoritas DJP Online; sejak 2025 default Coretax kecuali user menyebut layanan lama."},

    {"pemicu": "tidak bisa login", "kategori": "Autentikasi", "prioritas": 88,
     "terverifikasi": 1, "status": "aktif",
     "pola": ["tidak bisa login", "gak bisa login", "ga bisa masuk", "gagal login", "tidak bisa masuk", "error login"],
     "kandidat": [
        {"label": "Gagal login DJP Online", "sistem": "djp_online", "intent_bot": "Gagal_Login_DJPOnline", "petunjuk": "masa pajak <= 2024"},
        {"label": "Gagal login Coretax", "sistem": "coretax", "intent_bot": "Gagal_Login_Coretax", "petunjuk": "masa pajak >= 2025"},
        {"label": "Akun terkunci / perlu reset", "sistem": "lintas", "intent_bot": "Akun_Terkunci", "petunjuk": "user menyebut terkunci/terblokir"},
     ],
     "aturan_temporal": {"aktif": True, "cutoff": "2025-01-01", "sebelum": "djp_online", "sejak": "coretax"},
     "pertanyaan_klarifikasi": "Login ke DJP Online atau Coretax? Apakah muncul pesan error tertentu?"},

    {"pemicu": "minta efin", "kategori": "Autentikasi", "prioritas": 85,
     "terverifikasi": 1, "status": "aktif",
     "pola": ["minta efin", "mau efin", "butuh efin", "dapat efin", "urus efin"],
     "kandidat": [
        {"label": "Lupa EFIN", "sistem": "lintas", "intent_bot": "Lupa_EFIN", "petunjuk": "sudah pernah punya EFIN"},
        {"label": "Aktivasi / permintaan EFIN baru", "sistem": "lintas", "intent_bot": "Aktivasi_EFIN", "petunjuk": "belum pernah punya EFIN"},
        {"label": "Cetak ulang / reset EFIN", "sistem": "lintas", "intent_bot": "Reset_EFIN", "petunjuk": "EFIN ada tapi tidak bisa dipakai"},
     ],
     "aturan_temporal": {"aktif": False},
     "pertanyaan_klarifikasi": "Apakah Bapak/Ibu belum pernah punya EFIN (permintaan baru), lupa EFIN, atau EFIN tidak bisa dipakai?",
     "catatan": "EFIN lintas sistem, jangan diputus berdasarkan tanggal."},

    {"pemicu": "aktivasi", "kategori": "Aktivasi", "prioritas": 80,
     "terverifikasi": 1, "status": "aktif",
     "pola": ["aktivasi", "aktifkan", "cara aktivasi", "belum aktif"],
     "kandidat": [
        {"label": "Aktivasi NIK sebagai NPWP", "sistem": "coretax", "intent_bot": "Aktivasi_NIK", "petunjuk": "menyebut NIK/KTP"},
        {"label": "Aktivasi NPWP (non-efektif -> efektif)", "sistem": "lintas", "intent_bot": "Aktivasi_NPWP", "petunjuk": "menyebut NPWP non-efektif"},
        {"label": "Aktivasi EFIN", "sistem": "lintas", "intent_bot": "Aktivasi_EFIN", "petunjuk": "menyebut EFIN"},
        {"label": "Aktivasi akun (Coretax/DJP Online)", "sistem": "lintas", "intent_bot": "Aktivasi_Akun", "petunjuk": "menyebut akun/login"},
     ],
     "aturan_temporal": {"aktif": False},
     "pertanyaan_klarifikasi": "Yang ingin diaktivasi apa ya: NIK sebagai NPWP, NPWP, EFIN, atau akun (Coretax/DJP Online)?"},

    {"pemicu": "lapor pajak", "kategori": "Pelaporan", "prioritas": 70,
     "terverifikasi": 1, "status": "aktif",
     "pola": ["lapor pajak", "pelaporan", "cara lapor", "lapor spt", "submit spt"],
     "kandidat": [
        {"label": "Pelaporan via DJP Online (e-Filing)", "sistem": "djp_online", "intent_bot": "Lapor_DJPOnline", "petunjuk": "masa pajak <= 2024"},
        {"label": "Pelaporan via Coretax", "sistem": "coretax", "intent_bot": "Lapor_Coretax", "petunjuk": "masa pajak >= 2025"},
     ],
     "aturan_temporal": {"aktif": True, "cutoff": "2025-01-01", "sebelum": "djp_online", "sejak": "coretax"},
     "pertanyaan_klarifikasi": "Pelaporan untuk masa/tahun pajak kapan? (mulai 2025 lewat Coretax, sebelumnya DJP Online)"},

    {"pemicu": "bayar pajak", "kategori": "Pembayaran", "prioritas": 68,
     "terverifikasi": 1, "status": "aktif",
     "pola": ["bayar pajak", "pembayaran", "buat kode billing", "id billing", "setor pajak"],
     "kandidat": [
        {"label": "Pembayaran/billing via DJP Online / e-Billing", "sistem": "djp_online", "intent_bot": "Billing_DJPOnline", "petunjuk": "masa pajak <= 2024"},
        {"label": "Pembayaran/billing via Coretax", "sistem": "coretax", "intent_bot": "Billing_Coretax", "petunjuk": "masa pajak >= 2025"},
     ],
     "aturan_temporal": {"aktif": True, "cutoff": "2025-01-01", "sebelum": "djp_online", "sejak": "coretax"},
     "pertanyaan_klarifikasi": "Pembayaran untuk masa pajak kapan? (mulai 2025 kode billing dibuat di Coretax)"},

    {"pemicu": "buat faktur", "kategori": "Faktur", "prioritas": 55,
     "terverifikasi": 0, "status": "aktif",
     "pola": ["buat faktur", "faktur pajak", "e-faktur", "nomor faktur", "nomor seri faktur"],
     "kandidat": [
        {"label": "e-Faktur / NSFP via e-Nofa (lama)", "sistem": "e_nofa", "intent_bot": "Faktur_eNofa", "petunjuk": "masa pajak <= 2024"},
        {"label": "Faktur pajak via Coretax", "sistem": "coretax", "intent_bot": "Faktur_Coretax", "petunjuk": "masa pajak >= 2025"},
     ],
     "aturan_temporal": {"aktif": True, "cutoff": "2025-01-01", "sebelum": "e_nofa", "sejak": "coretax"},
     "pertanyaan_klarifikasi": "Penerbitan faktur untuk masa pajak kapan? Alur e-Nofa/e-Faktur (lama) atau Coretax (2025+)?",
     "catatan": "[PERLU VERIFIKASI TIM] pastikan alur faktur di Coretax vs e-Nofa sesuai kebijakan terbaru."},
]


def seed_defaults(conn, force=False):
    if force:
        conn.execute("DELETE FROM disambig")
        conn.commit()
    n = 0
    for item in SEED:
        try:
            upsert_rule(conn, item)
            n += 1
        except Exception as e:
            print("seed disambig gagal untuk", item.get("pemicu"), ":", e)
    return n


if __name__ == "__main__":
    c = init_db(connect())
    seeded = seed_defaults(c, force=True)
    print("disambig rows:", count(c), "(seeded:", seeded, ")")
    # smoke: match dua skenario
    m1 = match(c, "saya lupa password tidak bisa masuk", tanggal="2026-03-01")
    print("match 'lupa password' @2026:", [(x["pemicu"], x["keputusan_temporal"]) for x in m1])
    m2 = match(c, "lupa password", tanggal="2024-05-10")
    print("match 'lupa password' @2024:", [(x["pemicu"], x["keputusan_temporal"]) for x in m2])
    m3 = match(c, "mau minta efin dong", tanggal="2026-01-01")
    print("match 'minta efin':", [(x["pemicu"], len(x["kandidat"])) for x in m3])
    print("contoh konteks:\n" + build_context_text(m1))
