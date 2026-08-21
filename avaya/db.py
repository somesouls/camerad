# -*- coding: utf-8 -*-
"""
avaya_db.py
-----------
Lapisan penyimpanan (SQLite) untuk analitik AWE Avaya (percakapan Live-Chat
user <-> bot + agent).

DISENGAJA TERPISAH dari analytics_db.py (Dialogflow):
- File DB berbeda (default: avaya.db), env override: AVAYA_DB_FILE.
- Tidak ada penggabungan dengan data Dialogflow karena tidak ada ID unik yang
  sama antar kedua sumber (sesuai keputusan desain).

Tujuan: sekali analisis AWE dijalankan, hasilnya DISIMPAN supaya analis tidak
perlu upload/analisa ulang. Setiap kali analisis selesai -> 1 baris di awe_runs
(+ ledakan percakapan ke awe_conversations untuk query per-baris).

Hanya memakai stdlib (sqlite3 + json + hashlib + re). Tidak butuh server database.
"""
import os
import json as _json
import re as _re
import sqlite3
import hashlib as _hashlib
import datetime as _dt

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_db_path():
    return os.environ.get("AVAYA_DB_FILE") or os.path.join(_BASE_DIR, "avaya.db")


def _jkt_now_iso():
    try:
        from zoneinfo import ZoneInfo
        return _dt.datetime.now(ZoneInfo("Asia/Jakarta")).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        tz = _dt.timezone(_dt.timedelta(hours=7))
        return _dt.datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")


def connect(db_path=None):
    conn = sqlite3.connect(db_path or default_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    return conn


def init_db(conn):
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS awe_runs (
            id            TEXT PRIMARY KEY,
            label         TEXT,
            date_min      TEXT,
            date_max      TEXT,
            total_conv    INTEGER DEFAULT 0,
            total_cust    INTEGER DEFAULT 0,
            n_files       INTEGER DEFAULT 0,
            engine        TEXT,
            build         TEXT,
            source        TEXT DEFAULT 'upload',   -- 'upload' | 'pull'
            dashboard_json TEXT,                    -- blob dashboard lengkap
            records_json  TEXT,                     -- blob records mentah
            created_at    TEXT
        );
        CREATE TABLE IF NOT EXISTS awe_conversations (
            run_id        TEXT,
            sid           TEXT,
            tanggal       TEXT,
            customer      TEXT,
            agent_name    TEXT,
            agent_id      TEXT,
            durasi        INTEGER DEFAULT 0,
            behavior      TEXT,
            is_returning  TEXT,
            mapped_intent TEXT,
            coverage_band TEXT,
            case_label    TEXT,
            sentiment     TEXT,
            emotion       TEXT,
            topik         TEXT,
            deflection_gap INTEGER,
            PRIMARY KEY (run_id, sid)
        );
        CREATE INDEX IF NOT EXISTS idx_awe_conv_run ON awe_conversations(run_id);
        CREATE INDEX IF NOT EXISTS idx_awe_conv_intent ON awe_conversations(mapped_intent);
        CREATE TABLE IF NOT EXISTS awe_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS awe_day_coverage (
            day        TEXT PRIMARY KEY,   -- 'YYYY-MM-DD'
            run_id     TEXT,
            source     TEXT DEFAULT 'pull',
            total_conv INTEGER,
            pulled_at  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_awe_cov_run ON awe_day_coverage(run_id);
        CREATE TABLE IF NOT EXISTS awe_staging (
            sid          TEXT PRIMARY KEY,
            tanggal      TEXT,
            agent_id     TEXT,
            agent_name   TEXT,
            customer     TEXT,
            durasi       INTEGER DEFAULT 0,
            payload_json TEXT,
            batch_id     TEXT,
            pulled_by    TEXT,
            pulled_at    TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_awe_staging_tgl ON awe_staging(tanggal);
        CREATE TABLE IF NOT EXISTS awe_stage_batches (
            id         TEXT PRIMARY KEY,
            date_from  TEXT,
            date_to    TEXT,
            n_pulled   INTEGER DEFAULT 0,
            n_new      INTEGER DEFAULT 0,
            pulled_by  TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS awe_stage_coverage (
            day        TEXT PRIMARY KEY,
            batch_id   TEXT,
            total_conv INTEGER,
            pulled_by  TEXT,
            pulled_at  TEXT
        );
        """
    )
    _ensure_columns(cur, "awe_conversations", [
        ("topik", "topik TEXT"),
        ("deflection_gap", "deflection_gap INTEGER"),
        ("transkrip_json", "transkrip_json TEXT"),
        ("is_poro", "is_poro INTEGER"),
        ("jenis_layanan", "jenis_layanan TEXT"),
        ("nik", "nik TEXT"),
        ("non_npwp", "non_npwp INTEGER"),
        ("ss_salam_pembuka", "ss_salam_pembuka INTEGER"),
        ("ss_menanyakan_nama", "ss_menanyakan_nama INTEGER"),
        ("ss_menyapa_customer", "ss_menyapa_customer INTEGER"),
        ("ss_menawarkan_bantuan", "ss_menawarkan_bantuan INTEGER"),
        ("ss_hold", "ss_hold INTEGER"),
        ("ss_salam_penutup", "ss_salam_penutup INTEGER"),
        ("ss_lengkap", "ss_lengkap INTEGER"),
    ])
    conn.commit()
    # Migrasi ringan (sekali jalan): rapikan baris lama yang namanya berformat
    # 'Nama [NIK]'. Dijaga meta-flag agar tak berjalan tiap request.
    try:
        _backfill_nik(conn)
    except Exception:
        pass
    return conn


# =========================================================================
# SOFTSKILL SCORING (port dari assessor.js v4.2.0)
# Semua regex pakai _re.I agar case-insensitive.
# =========================================================================
_RE_SALAM_PEMBUKA  = _re.compile(r'\bselamat\s+(pagi|siang|sore|malam)\b', _re.I)
_RE_SALAM_PEMBUKA2 = _re.compile(r'perkenalkan\s+saya', _re.I)
_RE_MENANYAKAN_NAMA = _re.compile(
    r'(dengan\s+(bapak|ibu).*siapa|siapa\s+saya\s+terhubung'
    r'|boleh.*(tahu|saya).*nama|dengan\s+siapa\s+saya)', _re.I | _re.S)
_RE_MENAWARKAN = _re.compile(
    r'(ada(kah)?\s+(lagi\s+)?yang\s+(bisa|dapat)\s+(saya|kami)\s+bantu'
    r'|ada\s+yang\s+(bisa|dapat)\s+di?bantu'
    r'|(bisa|boleh)\s+(saya|kami)\s+bantu'
    r'|yang\s+(bisa|dapat)\s+(saya|kami)\s+bantu'
    r'|ada\s+(lagi\s+)?yang\s+(bisa|ingin)\s+(ditanyakan|disampaikan)'
    r'|silakan\s+(disampaikan|sampaikan)\s+(pertanyaan|keperluan|kendala|keluhan))',
    _re.I)
_RE_HOLD = _re.compile(
    r'(mohon|silakan)\s+menunggu'
    r'|kami\s+(cek|periksa|konfirmasi|telaah|pastikan)\s+(terlebih\s+dahulu|dahulu|ke)'
    r'|mohon\s+waktu'
    r'|terima\s+kasih\s+telah\s+bersedia\s+menunggu', _re.I)
_RE_SALAM_PENUTUP = _re.compile(
    r'(terima\s+kasih\s+telah\s+menggunakan|percakapan\s+ini\s+kami\s+akhiri'
    r'|selamat\s+beraktivitas|mari\s+wujudkan\s+djp|survei\s+kepuasan)', _re.I)
_RE_MENYAPA_SAPAAN = _re.compile(
    r'\b(bapak|ibu|pak|bu|kak|kakak|mas|mbak|saudara|sdr|sdri)\s+([a-zA-Z]{3,})', _re.I)
_STOP_MENYAPA = _re.compile(
    r'^(siapa|yang|dengan|untuk|silakan|di|ada|dapat|bisa|mohon|sudah|akan|atas|terkait'
    r'|sebelumnya|kami|saya|ini|itu|tersebut|segera|dalam|dan|atau|juga|agar|demi'
    r'|berkenan|mengisi)$', _re.I)
_RE_PORO_AFIRMASI = _re.compile(
    r'(pernyataan\s+afirmasi|kami\s+membutuhkan\s+afirmasi'
    r'|menyalin\s+ulang\s+pernyataan'
    r'|menyadari\s+sepenuhnya\s+akan\s+segala\s+akibatnya'
    r'|saya\s+menyatakan\s+bahwa\s+apa\s+yang\s+telah\s+saya'
    r'\s+(beritahukan|sampaikan)\s+adalah\s+benar)', _re.I)
_RE_PORO_VALIDASI = _re.compile(
    r'((validasi|verifikasi)\s+(terlebih\s+dahulu|data|identitas)'
    r'|memastikan\s+.{0,60}(mengajukan|pemohon|permohonan)'
    r'|(silakan|mohon)\s+.{0,40}(isikan|lengkapi|isi)\s+data\s+berikut)',
    _re.I | _re.S)
_PORO_FIELDS = [
    _re.compile(r'\bNPWP\b', _re.I),
    _re.compile(r'\bNIK\b', _re.I),
    _re.compile(r'email\s+terdaftar', _re.I),
    _re.compile(r'(telepon|no\.?\s*hp|nomor\s+hp|no\s+hp)\b.{0,20}terdaftar', _re.I | _re.S),
    _re.compile(r'tanggal\s+pelaporan\s+spt|spt\s+tahunan\s+terakhir', _re.I),
    _re.compile(r'alamat\b.{0,25}(tinggal|terdaftar)', _re.I | _re.S),
]
_LAYANAN = [
    # Urutan = prioritas (first-match). Pola spesifik diletakkan di atas pola umum.
    (_re.compile(r'lupa\s*efin|(lupa|hilang|tidak\s+tahu|nggak?\s+tau|ga\s+tau|belum\s+punya).{0,15}efin|efin.{0,15}(lupa|hilang|tidak\s+tahu)', _re.I | _re.S), "Lupa EFIN"),
    (_re.compile(r'aktivasi\s+efin|permohonan\s+efin|efin.{0,15}(belum\s+aktif|aktivasi|permohonan|minta|daftar)', _re.I | _re.S), "Aktivasi EFIN"),
    (_re.compile(r'(perubahan|pemutakhiran|pembaruan|ubah|mengubah|update|ganti|mengganti)\s+(data|e-?mail|email|nomor\s+(hp|handphone|telepon|telpon|ponsel)|no\.?\s*(hp|telp)|alamat|kontak)|\bpdmnpwp\b', _re.I | _re.S), "Perubahan Data"),
    (_re.compile(r'aktivasi\s+(akun|coretax)|akun\s+coretax.{0,15}(aktif|aktivasi)', _re.I | _re.S), "Aktivasi Akun"),
    (_re.compile(r'(lupa|reset|ubah|atur\s+ulang|mengatur\s+ulang)\s+(kata\s+sandi|password|sandi|kata\s+kunci)', _re.I | _re.S), "Reset/Lupa Password"),
    (_re.compile(r'(lapor|pelaporan|melaporkan|mengisi|pengisian)\s+spt|spt\s+(tahunan|masa)|e-?filing', _re.I | _re.S), "Pelaporan SPT/e-Filing"),
    (_re.compile(r'kode\s+billing|e-?billing|(buat|bikin|membuat|generate)\s+.{0,12}billing|\bntpn\b|(bayar|pembayaran)\s+pajak', _re.I | _re.S), "Pembayaran/Kode Billing"),
    (_re.compile(r'e-?faktur|faktur\s+pajak|e-?nofa|nomor\s+seri\s+faktur|\bnsfp\b', _re.I | _re.S), "Faktur Pajak/e-Faktur"),
    (_re.compile(r'sertifikat\s+elektronik|\bsertel\b|passphrase', _re.I), "Sertifikat Elektronik"),
    (_re.compile(r'cetak\s+(ulang\s+)?(kartu\s+)?npwp', _re.I), "Cetak NPWP"),
    (_re.compile(r'kode\s+otorisasi', _re.I), "Kode Otorisasi DJP"),
    (_re.compile(r'(penonaktifan|non\s*efektif|nonaktif|mengaktifkan\s+kembali|aktifkan\s+kembali).{0,20}npwp|npwp.{0,20}(non\s*efektif|nonaktif|penonaktifan|\bne\b)', _re.I | _re.S), "Aktivasi/Nonaktif NPWP"),
    (_re.compile(r'(daftar|pendaftaran|mendaftar|buat|membuat|registrasi|bikin)\s+npwp|npwp\s+baru', _re.I | _re.S), "Pendaftaran NPWP"),
    (_re.compile(r'pengukuhan\s+(pkp|pengusaha)|pengusaha\s+kena\s+pajak', _re.I), "PKP"),
]
_BOT_ROLES  = {'bot', 'ccai', 'chatbot', 'virtual assistant'}
_CUST_ROLES = {'customer', 'cust', 'pelanggan', 'user'}
_BOT_NAME_RE = _re.compile(r'ccai|chatbot|virtual\s+assistant|google', _re.I)
_BOT_PHRASE  = _re.compile(
    r'virtual\s+assistant\s+\(chat\s+bot\)|petugas\s+kami\s+akan\s+segera\s+membantu', _re.I)


def _is_agent(role, text):
    r = (role or "").strip().lower()
    if r in _CUST_ROLES:
        return False
    if r in _BOT_ROLES or _BOT_NAME_RE.search(r):
        return False
    if _BOT_PHRASE.search(text or ""):
        return False
    return True


def _match_menyapa(t, cust_name=""):
    if cust_name:
        tokens = [w for w in cust_name.split() if len(w) >= 3]
        for tok in tokens:
            pref = tok[:max(4, int(len(tok) * 0.6))]
            if _re.search(r'\b' + _re.escape(pref), t, _re.I):
                return True
    for m in _RE_MENYAPA_SAPAAN.finditer(t):
        if not _STOP_MENYAPA.match(m.group(2)):
            return True
    return False


def _detect_poro(all_text):
    if _RE_PORO_AFIRMASI.search(all_text):
        return True
    if _RE_PORO_VALIDASI.search(all_text):
        n = sum(1 for f in _PORO_FIELDS if f.search(all_text))
        if n >= 3:
            return True
    return False


def _detect_layanan(all_text):
    for pat, nm in _LAYANAN:
        if pat.search(all_text):
            return nm
    return None


def score_softskill(transkrip, cust_name=""):
    """Skor softskill dari list [{role, text}].

    Returns dict:
      salam_pembuka, menanyakan_nama, menyapa_customer, menawarkan_bantuan,
      hold, salam_penutup (semua bool); lengkap (bool -- semua wajib lolos);
      is_poro (bool); jenis_layanan (str|None).
    Kembalikan None bila tidak ada pesan agent sama sekali.
    """
    if not transkrip:
        return None
    agent_msgs = [
        m.get("text", "") for m in transkrip
        if isinstance(m, dict) and _is_agent(m.get("role", ""), m.get("text", ""))
    ]
    if not agent_msgs:
        return None
    awal  = "\n".join(agent_msgs[:2])
    akhir = "\n".join(agent_msgs[-2:])
    semua = "\n".join(agent_msgs)
    all_text = "\n".join(m.get("text", "") for m in transkrip if isinstance(m, dict))
    ss = {
        "salam_pembuka":      bool(_RE_SALAM_PEMBUKA.search(awal) or _RE_SALAM_PEMBUKA2.search(awal)),
        "menanyakan_nama":    bool(_RE_MENANYAKAN_NAMA.search(semua)),
        "menyapa_customer":   _match_menyapa(semua, cust_name),
        "menawarkan_bantuan": bool(_RE_MENAWARKAN.search(semua)),
        "hold":               bool(_RE_HOLD.search(semua)),
        "salam_penutup":      bool(_RE_SALAM_PENUTUP.search(akhir)),
    }
    _wajib = ["salam_pembuka", "menanyakan_nama", "menyapa_customer",
               "menawarkan_bantuan", "salam_penutup"]
    ss["lengkap"]      = all(ss[k] for k in _wajib)
    ss["is_poro"]      = _detect_poro(all_text)
    ss["jenis_layanan"] = _detect_layanan(all_text)
    return ss


# ---- util ekstraksi field dashboard (defensif thd variasi nama kunci) ------
def _g(d, *keys, default=""):
    for k in keys:
        if isinstance(d, dict) and k in d and d[k] not in (None, ""):
            return d[k]
    return default


# ---- util NIK/NPWP (nama customer Avaya berformat "Nama [NIK/NPWP]") -------
def _nik_from_str(s):
    """Ambil digit di dalam kurung siku: 'Budi [3210..]' -> '3210..'."""
    s = str(s or "")
    a, b = s.find("["), s.find("]")
    return s[a + 1:b].strip() if (a >= 0 and b > a) else ""


def _name_wo_nik(s):
    """Buang bagian '[..]' dari nama customer bila ada."""
    s = str(s or "")
    i = s.find("[")
    return (s[:i] if i >= 0 else s).strip()


def _nonnpwp_flag(nik):
    """1 bila NIK/NPWP tidak valid (kosong/nol/non-digit), 0 bila tampak valid,
    None bila tak ada info."""
    nik = str(nik or "").strip()
    if not nik:
        return None
    valid = nik.isdigit() and not all(ch == "0" for ch in nik)
    return 0 if valid else 1


def _nik_of(obj):
    """Resolusi NIK/NPWP dari sebuah dict percakapan (record pull / staging)."""
    v = str(_g(obj, "nik", "npwp", "NIK", "NPWP", default="")).strip()
    if v:
        return v
    return _nik_from_str(_g(obj, "customer", "customerRaw", "pelanggan", default=""))


def _ensure_columns(cur, table, coldefs):
    """Migrasi ringan: tambah kolom yang belum ada (ALTER TABLE ADD COLUMN)."""
    have = {r[1] for r in cur.execute("PRAGMA table_info(%s)" % table).fetchall()}
    for name, ddl in coldefs:
        if name not in have:
            cur.execute("ALTER TABLE %s ADD COLUMN %s" % (table, ddl))


def _backfill_nik(conn):
    """Migrasi ringan sekali-jalan: rapikan baris yang namanya masih berformat
    'Nama [NIK]' menjadi kolom nik + nama bersih.

    PENTING: sengaja RINGAN -- hanya menyentuh baris dgn kurung '[..]' pada
    kolom customer, lalu SELALU menandai 'done' agar TIDAK berjalan berulang
    tiap request. (Versi lama memindai SELURUH baris + lookup staging pada tiap
    init_db; bila gagal karena lock, flag tak pernah di-set sehingga diulang
    terus -> penyebab utama 'database is locked'.) Pemulihan berat dari staging
    dipindah ke fungsi manual nik_backfill() yang dipicu dari UI Kelola Data.
    """
    if get_meta(conn, "nik_backfill_v1") == "done":
        return 0
    cur = conn.cursor()
    have = {r[1] for r in cur.execute("PRAGMA table_info(awe_conversations)").fetchall()}
    if "nik" not in have:
        set_meta(conn, "nik_backfill_v1", "done")
        conn.commit()
        return 0
    n = 0
    try:
        rows = cur.execute(
            "SELECT rowid, customer FROM awe_conversations "
            "WHERE (nik IS NULL OR nik='') AND customer LIKE '%[%]%'"
        ).fetchall()
        for r in rows:
            nik = _nik_from_str(r["customer"])
            clean = _name_wo_nik(r["customer"])
            if nik:
                cur.execute(
                    "UPDATE awe_conversations SET nik=?, non_npwp=?, customer=? "
                    "WHERE rowid=?",
                    (nik, _nonnpwp_flag(nik), clean, r["rowid"]),
                )
                n += 1
    except Exception:
        pass
    # Selalu tandai selesai supaya migrasi otomatis tidak berulang tiap request.
    set_meta(conn, "nik_backfill_v1", "done")
    conn.commit()
    return n


def nik_stats(conn):
    """Ringkasan cakupan NIK/NPWP pada awe_conversations (untuk UI Kelola Data)."""
    cur = conn.cursor()
    have = {r[1] for r in cur.execute("PRAGMA table_info(awe_conversations)").fetchall()}
    if "nik" not in have:
        try:
            staged = cur.execute("SELECT COUNT(*) FROM awe_staging").fetchone()[0]
        except Exception:
            staged = 0
        return {"total": 0, "with_nik": 0, "without_nik": 0,
                "name_has_bracket": 0, "recover_from_name": 0, "staging_rows": staged}
    total = cur.execute("SELECT COUNT(*) FROM awe_conversations").fetchone()[0]
    with_nik = cur.execute(
        "SELECT COUNT(*) FROM awe_conversations WHERE nik IS NOT NULL AND nik!=''"
    ).fetchone()[0]
    name_bracket = cur.execute(
        "SELECT COUNT(*) FROM awe_conversations WHERE customer LIKE '%[%]%'"
    ).fetchone()[0]
    recover_from_name = cur.execute(
        "SELECT COUNT(*) FROM awe_conversations "
        "WHERE (nik IS NULL OR nik='') AND customer LIKE '%[%]%'"
    ).fetchone()[0]
    staged = cur.execute("SELECT COUNT(*) FROM awe_staging").fetchone()[0]
    return {"total": total, "with_nik": with_nik, "without_nik": total - with_nik,
            "name_has_bracket": name_bracket, "recover_from_name": recover_from_name,
            "staging_rows": staged}


def nik_backfill(conn, batch_commit=2000):
    """Isi kolom nik/non_npwp untuk baris awe_conversations yang belum ber-NIK.

    Sumber (berurutan): kurung '[..]' pada kolom customer, lalu payload_json di
    awe_staging (dicocokkan by sid). Nama yang masih mengandung '[..]' sekaligus
    dirapikan. Idempoten (aman diulang) & dijalankan bertahap dengan commit
    per-batch supaya kunci tulis tidak dipegang lama (mengatasi 'database is
    locked' pada data besar).

    Returns: {scanned, nik_filled, name_cleaned}
    """
    cur = conn.cursor()
    have = {r[1] for r in cur.execute("PRAGMA table_info(awe_conversations)").fetchall()}
    if "nik" not in have:
        return {"scanned": 0, "nik_filled": 0, "name_cleaned": 0}
    # Longgarkan timeout khusus operasi berat ini.
    try:
        conn.execute("PRAGMA busy_timeout=60000;")
    except Exception:
        pass
    rows = cur.execute(
        "SELECT rowid, sid, customer, nik FROM awe_conversations "
        "WHERE nik IS NULL OR nik='' OR customer LIKE '%[%]%'"
    ).fetchall()
    scanned = len(rows)
    look = conn.cursor()   # cursor terpisah untuk lookup staging
    n_nik = 0
    n_name = 0
    processed = 0
    for r in rows:
        rid = r["rowid"]
        sid = str(r["sid"] or "").strip()
        cust = r["customer"]
        cur_nik = str(r["nik"] or "").strip()
        nik = cur_nik or _nik_from_str(cust)
        if not nik and sid:
            sr = look.execute(
                "SELECT payload_json FROM awe_staging WHERE sid=?", (sid,)
            ).fetchone()
            if sr and sr["payload_json"]:
                try:
                    p = _json.loads(sr["payload_json"])
                except Exception:
                    p = None
                if isinstance(p, dict):
                    nik = _nik_of(p)
        orig = "" if cust is None else str(cust)
        clean = _name_wo_nik(cust)
        sets = []
        params = []
        if nik and not cur_nik:
            sets.append("nik=?")
            params.append(nik)
            sets.append("non_npwp=?")
            params.append(_nonnpwp_flag(nik))
            n_nik += 1
        if clean and clean != orig:
            sets.append("customer=?")
            params.append(clean)
            n_name += 1
        if sets:
            params.append(rid)
            cur.execute(
                "UPDATE awe_conversations SET " + ",".join(sets) + " WHERE rowid=?",
                params,
            )
            processed += 1
            if processed % batch_commit == 0:
                conn.commit()
    conn.commit()
    return {"scanned": scanned, "nik_filled": n_nik, "name_cleaned": n_name}


def _gap_flag(c):
    v = _g(c, "deflection_gap", "deflectionGap", default=None)
    if v is None:
        return None
    return 1 if str(v).strip().lower() in ("1", "true", "ya", "yes", "y") else 0


def _extract_transkrip(obj):
    """Ambil daftar {role,text} dari dict. Kembalikan list atau None."""
    if not isinstance(obj, dict):
        return None
    t = obj.get("transkrip")
    if t in (None, ""):
        t = obj.get("transcript")
    if not isinstance(t, list) or not t:
        return None
    out = []
    for m in t:
        if isinstance(m, dict):
            role = str(_g(m, "role", "speaker", "peran", "from", default="")).strip()
            text = _g(m, "text", "message", "isi", "pesan", "content", default="")
            out.append({"role": role, "text": str(text)})
        elif isinstance(m, str) and m.strip():
            out.append({"role": "", "text": m})
    return out or None


def _make_run_id(dashboard, records):
    meta = dashboard.get("meta", {}) if isinstance(dashboard, dict) else {}
    convs = dashboard.get("conversations", []) if isinstance(dashboard, dict) else []
    sids = sorted(str(_g(c, "sid", "Sid", default="")) for c in convs if isinstance(c, dict))
    basis = "|".join([
        str(_g(meta, "date_min", "tanggal_min", default="")),
        str(_g(meta, "date_max", "tanggal_max", default="")),
        str(_g(meta, "total_conv", "total", default=len(convs))),
        ",".join(sids),
    ])
    return _hashlib.sha1(basis.encode("utf-8", "replace")).hexdigest()[:16]


def _days_in_range(day_from, day_to):
    try:
        a = _dt.date.fromisoformat(str(day_from)[:10])
        b = _dt.date.fromisoformat(str(day_to)[:10])
    except Exception:
        return []
    if b < a:
        a, b = b, a
    out, d = [], a
    while d <= b:
        out.append(d.isoformat())
        d += _dt.timedelta(days=1)
    return out


def _mark_days(cur, days, run_id, source, total_conv):
    now = _jkt_now_iso()
    for d in days:
        if not d:
            continue
        cur.execute(
            "INSERT INTO awe_day_coverage(day,run_id,source,total_conv,pulled_at) "
            "VALUES(?,?,?,?,?) ON CONFLICT(day) DO UPDATE SET "
            "run_id=excluded.run_id, source=excluded.source, "
            "total_conv=excluded.total_conv, pulled_at=excluded.pulled_at",
            (d, run_id, source, total_conv, now),
        )


def mark_days_covered(conn, day_from, day_to, run_id=None, source="pull", total_conv=None):
    cur = conn.cursor()
    _mark_days(cur, _days_in_range(day_from, day_to), run_id, source, total_conv)
    conn.commit()
    return _days_in_range(day_from, day_to)


def covered_days(conn):
    cur = conn.cursor()
    rs = cur.execute("SELECT day FROM awe_day_coverage").fetchall()
    return set(r["day"] for r in rs)


def coverage_for_range(conn, day_from, day_to):
    req = _days_in_range(day_from, day_to)
    cur = conn.cursor()
    cov_map = {}
    if req:
        qs = ",".join("?" for _ in req)
        rs = cur.execute(
            "SELECT day, run_id FROM awe_day_coverage WHERE day IN (%s)" % qs, req
        ).fetchall()
        for r in rs:
            cov_map[r["day"]] = r["run_id"]
    covered = [d for d in req if d in cov_map]
    missing = [d for d in req if d not in cov_map]
    run_ids = [rid for rid in dict.fromkeys(cov_map.values()) if rid]
    runs = []
    for rid in run_ids:
        rr = get_run(conn, rid)
        if rr:
            runs.append({k: rr[k] for k in ("id", "label", "date_min", "date_max",
                                             "total_conv", "total_cust", "source", "created_at")})
    return {"requested": req, "covered": covered, "missing": missing, "runs": runs}


def save_run(conn, dashboard, records=None, label=None, n_files=0, source="upload", build=None,
             cover_from=None, cover_to=None):
    """Simpan satu hasil analisis AWE. Idempoten: run_id sama -> ditimpa."""
    if not isinstance(dashboard, dict):
        raise ValueError("dashboard harus dict")
    records = records or []
    meta = dashboard.get("meta", {}) or {}
    convs = dashboard.get("conversations", []) or []
    run_id = _make_run_id(dashboard, records)
    date_min = str(_g(meta, "date_min", "tanggal_min", default=""))
    date_max = str(_g(meta, "date_max", "tanggal_max", default=""))
    total_conv = int(_g(meta, "total_conv", "total", default=len(convs)) or len(convs))
    total_cust = int(_g(meta, "total_customers", "total_cust", default=0) or 0)
    engine = str(_g(meta, "engine", default=""))
    if not label:
        label = ("%s s/d %s" % (date_min, date_max)) if date_min or date_max else "Analisis AWE"

    cur = conn.cursor()
    exists = cur.execute("SELECT 1 FROM awe_runs WHERE id=?", (run_id,)).fetchone() is not None
    cur.execute(
        """INSERT INTO awe_runs
             (id,label,date_min,date_max,total_conv,total_cust,n_files,engine,build,source,dashboard_json,records_json,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
             label=excluded.label, date_min=excluded.date_min, date_max=excluded.date_max,
             total_conv=excluded.total_conv, total_cust=excluded.total_cust, n_files=excluded.n_files,
             engine=excluded.engine, build=excluded.build, source=excluded.source,
             dashboard_json=excluded.dashboard_json, records_json=excluded.records_json,
             created_at=excluded.created_at
        """,
        (run_id, label, date_min, date_max, total_conv, total_cust, int(n_files or 0),
         engine, build or "", source,
         _json.dumps(dashboard, ensure_ascii=False),
         _json.dumps(records, ensure_ascii=False),
         _jkt_now_iso()),
    )
    cur.execute("DELETE FROM awe_conversations WHERE run_id=?", (run_id,))

    # Transkrip + NIK per-sid (dari records mentah hasil pull; nik sudah ada di
    # setiap objek percakapan hasil client.build_conv).
    tx_by_sid = {}
    nik_by_sid = {}
    for rec in records:
        if not isinstance(rec, dict):
            continue
        rsid = str(_g(rec, "sid", "Sid", default="")).strip()
        if not rsid:
            continue
        if rsid not in tx_by_sid:
            rtx = _extract_transkrip(rec)
            if rtx:
                tx_by_sid[rsid] = rtx
        if rsid not in nik_by_sid:
            rnik = _nik_of(rec)
            if rnik:
                nik_by_sid[rsid] = rnik
    need = [str(_g(c, "sid", "Sid", default="")).strip() for c in convs if isinstance(c, dict)]
    need = [s for s in need if s and s not in tx_by_sid]
    for i in range(0, len(need), 400):
        chunk = need[i:i + 400]
        qs = ",".join("?" for _ in chunk)
        for sr in cur.execute(
            "SELECT sid, payload_json FROM awe_staging WHERE sid IN (%s)" % qs, chunk
        ).fetchall():
            try:
                _payload = _json.loads(sr["payload_json"])
            except Exception:
                _payload = None
            stx = _extract_transkrip(_payload) if _payload else None
            if stx:
                tx_by_sid[str(sr["sid"])] = stx
            if _payload and str(sr["sid"]) not in nik_by_sid:
                _rn = _nik_of(_payload)
                if _rn:
                    nik_by_sid[str(sr["sid"])] = _rn

    rows = []
    for c in convs:
        if not isinstance(c, dict):
            continue
        csid      = str(_g(c, "sid", "Sid", default=""))
        ctx       = tx_by_sid.get(csid.strip()) or _extract_transkrip(c)
        cust_raw  = str(_g(c, "customer", "pelanggan", default=""))
        cust_name = _name_wo_nik(cust_raw)
        c_nik     = (str(_g(c, "nik", "npwp", "NIK", "NPWP", default="")).strip()
                     or nik_by_sid.get(csid.strip(), "")
                     or _nik_from_str(cust_raw))
        _nn = _g(c, "nonNpwp", "non_npwp", default=None)
        if _nn is None:
            c_nonnpwp = _nonnpwp_flag(c_nik)
        else:
            c_nonnpwp = 1 if str(_nn).strip().lower() in ("1", "true", "ya", "yes", "y") else 0

        # --- Softskill scoring ---
        ss = score_softskill(ctx or [], cust_name) if ctx else None
        all_text = "\n".join(m.get("text", "") for m in ctx if isinstance(m, dict)) if ctx else ""
        is_poro       = (1 if _detect_poro(all_text) else 0) if ctx else None
        jenis_layanan = _detect_layanan(all_text) if ctx else None
        def _bi(val):
            return 1 if val else 0
        if ss is not None:
            ss_salam_pembuka      = _bi(ss["salam_pembuka"])
            ss_menanyakan_nama    = _bi(ss["menanyakan_nama"])
            ss_menyapa_customer   = _bi(ss["menyapa_customer"])
            ss_menawarkan_bantuan = _bi(ss["menawarkan_bantuan"])
            ss_hold               = _bi(ss["hold"])
            ss_salam_penutup      = _bi(ss["salam_penutup"])
            ss_lengkap            = _bi(ss["lengkap"])
        else:
            ss_salam_pembuka = ss_menanyakan_nama = ss_menyapa_customer = None
            ss_menawarkan_bantuan = ss_hold = ss_salam_penutup = ss_lengkap = None

        rows.append((
            run_id,
            csid,
            str(_g(c, "tanggal", "date", "start", default="")),
            cust_name,
            str(_g(c, "agent_name", "agent", "agentName", default="")),
            str(_g(c, "agent_id", "agentId", default="")),
            int(_g(c, "durasi", "duration", "duration_seconds", default=0) or 0),
            str(_g(c, "behavior", "perilaku", default="")),
            str(_g(c, "returning", "balik", default="")),
            str(_g(c, "mapped_intent", "intent", default="")),
            str(_g(c, "coverage_band", "coverage", "band", default="")),
            str(_g(c, "case_label", "case", "kasus", default="")),
            str(_g(c, "sentiment", "sentimen", default="")),
            str(_g(c, "emotion", "emosi", default="")),
            (str(_g(c, "topik", "topic", default="")) or None),
            _gap_flag(c),
            (_json.dumps(ctx, ensure_ascii=False) if ctx else None),
            is_poro,
            jenis_layanan,
            (c_nik or None),
            c_nonnpwp,
            ss_salam_pembuka,
            ss_menanyakan_nama,
            ss_menyapa_customer,
            ss_menawarkan_bantuan,
            ss_hold,
            ss_salam_penutup,
            ss_lengkap,
        ))
    if rows:
        cur.executemany(
            """INSERT OR REPLACE INTO awe_conversations
                 (run_id,sid,tanggal,customer,agent_name,agent_id,durasi,behavior,
                  is_returning,mapped_intent,coverage_band,case_label,sentiment,emotion,
                  topik,deflection_gap,transkrip_json,
                  is_poro,jenis_layanan,nik,non_npwp,
                  ss_salam_pembuka,ss_menanyakan_nama,ss_menyapa_customer,
                  ss_menawarkan_bantuan,ss_hold,ss_salam_penutup,ss_lengkap)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
    if cover_from and cover_to:
        _mark_days(cur, _days_in_range(cover_from, cover_to), run_id, source, total_conv)
    else:
        _cdays = sorted({str(_g(c, "tanggal", "date", "start", default=""))[:10]
                         for c in convs if isinstance(c, dict)})
        _mark_days(cur, [d for d in _cdays if d], run_id, source, None)
    set_meta(conn, "last_saved_at", _jkt_now_iso())
    conn.commit()
    return {"id": run_id, "total_conv": total_conv, "total_cust": total_cust,
            "date_min": date_min, "date_max": date_max, "new": not exists}


def list_runs(conn, limit=100):
    cur = conn.cursor()
    rs = cur.execute(
        """SELECT id,label,date_min,date_max,total_conv,total_cust,n_files,engine,
                  build,source,created_at
           FROM awe_runs ORDER BY datetime(created_at) DESC LIMIT ?""",
        (int(limit),),
    ).fetchall()
    return [dict(r) for r in rs]


def get_run(conn, run_id, with_records=False):
    cur = conn.cursor()
    r = cur.execute("SELECT * FROM awe_runs WHERE id=?", (run_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    out = {
        "id": d["id"], "label": d["label"], "date_min": d["date_min"],
        "date_max": d["date_max"], "total_conv": d["total_conv"],
        "total_cust": d["total_cust"], "n_files": d["n_files"],
        "engine": d["engine"], "build": d["build"], "source": d["source"],
        "created_at": d["created_at"],
        "dashboard": _json.loads(d["dashboard_json"] or "{}"),
    }
    if with_records:
        out["records"] = _json.loads(d["records_json"] or "[]")
    return out


def get_transcript(conn, sid, run_id=None):
    """Baca transkrip satu percakapan untuk Penilaian QA."""
    sid = str(sid or "").strip()
    if not sid:
        return None
    cur = conn.cursor()
    row = None
    if run_id:
        row = cur.execute(
            "SELECT run_id, sid, transkrip_json, customer, nik, agent_name, agent_id, "
            "is_poro, jenis_layanan, ss_salam_pembuka, ss_menanyakan_nama, "
            "ss_menyapa_customer, ss_menawarkan_bantuan, ss_hold, ss_salam_penutup, ss_lengkap "
            "FROM awe_conversations WHERE run_id=? AND sid=?", (run_id, sid),
        ).fetchone()
    if row is None:
        row = cur.execute(
            "SELECT run_id, sid, transkrip_json, customer, nik, agent_name, agent_id, "
            "is_poro, jenis_layanan, ss_salam_pembuka, ss_menanyakan_nama, "
            "ss_menyapa_customer, ss_menawarkan_bantuan, ss_hold, ss_salam_penutup, ss_lengkap "
            "FROM awe_conversations "
            "WHERE sid=? AND transkrip_json IS NOT NULL "
            "ORDER BY rowid DESC LIMIT 1", (sid,),
        ).fetchone()
    if row is None:
        row = cur.execute(
            "SELECT run_id, sid, transkrip_json, customer, nik, agent_name, agent_id, "
            "is_poro, jenis_layanan, ss_salam_pembuka, ss_menanyakan_nama, "
            "ss_menyapa_customer, ss_menawarkan_bantuan, ss_hold, ss_salam_penutup, ss_lengkap "
            "FROM awe_conversations "
            "WHERE sid=? ORDER BY rowid DESC LIMIT 1", (sid,),
        ).fetchone()
    if row is not None and row["transkrip_json"]:
        try:
            tx = _json.loads(row["transkrip_json"])
        except Exception:
            tx = None
        if tx:
            d = dict(row)
            return {
                "sid": sid, "run_id": d["run_id"], "source": "database",
                "customer": d.get("customer") or "",
                "nik": d.get("nik") or "",
                "agent_name": d.get("agent_name") or "",
                "agent_id": d.get("agent_id") or "",
                "is_poro": d.get("is_poro"),
                "jenis_layanan": d.get("jenis_layanan") or "",
                "softskill": {
                    "salam_pembuka":      d.get("ss_salam_pembuka"),
                    "menanyakan_nama":    d.get("ss_menanyakan_nama"),
                    "menyapa_customer":   d.get("ss_menyapa_customer"),
                    "menawarkan_bantuan": d.get("ss