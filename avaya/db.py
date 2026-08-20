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
    conn.execute("PRAGMA busy_timeout=8000;")
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
        ("ss_salam_pembuka", "ss_salam_pembuka INTEGER"),
        ("ss_menanyakan_nama", "ss_menanyakan_nama INTEGER"),
        ("ss_menyapa_customer", "ss_menyapa_customer INTEGER"),
        ("ss_menawarkan_bantuan", "ss_menawarkan_bantuan INTEGER"),
        ("ss_hold", "ss_hold INTEGER"),
        ("ss_salam_penutup", "ss_salam_penutup INTEGER"),
        ("ss_lengkap", "ss_lengkap INTEGER"),
    ])
    conn.commit()
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


def _ensure_columns(cur, table, coldefs):
    """Migrasi ringan: tambah kolom yang belum ada (ALTER TABLE ADD COLUMN)."""
    have = {r[1] for r in cur.execute("PRAGMA table_info(%s)" % table).fetchall()}
    for name, ddl in coldefs:
        if name not in have:
            cur.execute("ALTER TABLE %s ADD COLUMN %s" % (table, ddl))


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

    # Transkrip per-sid
    tx_by_sid = {}
    for rec in records:
        if not isinstance(rec, dict):
            continue
        rsid = str(_g(rec, "sid", "Sid", default="")).strip()
        if not rsid or rsid in tx_by_sid:
            continue
        rtx = _extract_transkrip(rec)
        if rtx:
            tx_by_sid[rsid] = rtx
    need = [str(_g(c, "sid", "Sid", default="")).strip() for c in convs if isinstance(c, dict)]
    need = [s for s in need if s and s not in tx_by_sid]
    for i in range(0, len(need), 400):
        chunk = need[i:i + 400]
        qs = ",".join("?" for _ in chunk)
        for sr in cur.execute(
            "SELECT sid, payload_json FROM awe_staging WHERE sid IN (%s)" % qs, chunk
        ).fetchall():
            try:
                stx = _extract_transkrip(_json.loads(sr["payload_json"]))
            except Exception:
                stx = None
            if stx:
                tx_by_sid[str(sr["sid"])] = stx

    rows = []
    for c in convs:
        if not isinstance(c, dict):
            continue
        csid      = str(_g(c, "sid", "Sid", default=""))
        ctx       = tx_by_sid.get(csid.strip()) or _extract_transkrip(c)
        cust_name = str(_g(c, "customer", "pelanggan", default=""))

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
                  is_poro,jenis_layanan,
                  ss_salam_pembuka,ss_menanyakan_nama,ss_menyapa_customer,
                  ss_menawarkan_bantuan,ss_hold,ss_salam_penutup,ss_lengkap)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
            "SELECT run_id, sid, transkrip_json, customer, agent_name, agent_id, "
            "is_poro, jenis_layanan, ss_salam_pembuka, ss_menanyakan_nama, "
            "ss_menyapa_customer, ss_menawarkan_bantuan, ss_hold, ss_salam_penutup, ss_lengkap "
            "FROM awe_conversations WHERE run_id=? AND sid=?", (run_id, sid),
        ).fetchone()
    if row is None:
        row = cur.execute(
            "SELECT run_id, sid, transkrip_json, customer, agent_name, agent_id, "
            "is_poro, jenis_layanan, ss_salam_pembuka, ss_menanyakan_nama, "
            "ss_menyapa_customer, ss_menawarkan_bantuan, ss_hold, ss_salam_penutup, ss_lengkap "
            "FROM awe_conversations "
            "WHERE sid=? AND transkrip_json IS NOT NULL "
            "ORDER BY rowid DESC LIMIT 1", (sid,),
        ).fetchone()
    if row is None:
        row = cur.execute(
            "SELECT run_id, sid, transkrip_json, customer, agent_name, agent_id, "
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
                "agent_name": d.get("agent_name") or "",
                "agent_id": d.get("agent_id") or "",
                "is_poro": d.get("is_poro"),
                "jenis_layanan": d.get("jenis_layanan") or "",
                "softskill": {
                    "salam_pembuka":      d.get("ss_salam_pembuka"),
                    "menanyakan_nama":    d.get("ss_menanyakan_nama"),
                    "menyapa_customer":   d.get("ss_menyapa_customer"),
                    "menawarkan_bantuan": d.get("ss_menawarkan_bantuan"),
                    "hold":               d.get("ss_hold"),
                    "salam_penutup":      d.get("ss_salam_penutup"),
                    "lengkap":            d.get("ss_lengkap"),
                },
                "transkrip": tx,
            }
    # fallback ke staging
    rs = cur.execute(
        "SELECT payload_json FROM awe_staging WHERE sid=?", (sid,)
    ).fetchone()
    if rs is not None:
        try:
            tx = _extract_transkrip(_json.loads(rs["payload_json"]))
        except Exception:
            tx = None
        if tx:
            return {"sid": sid, "run_id": (run_id or ""), "source": "staging",
                    "customer": "", "agent_name": "", "agent_id": "",
                    "is_poro": None, "jenis_layanan": "", "softskill": {},
                    "transkrip": tx}
    return None


def list_for_assess(conn, range_="all", start="", end="", agent="", poro="",
                    jenis="", ss_lengkap="", ss_attrs=None, limit=200):
    """Query percakapan untuk Penilaian QA dengan filter softskill.

    Default range_='all' (mengikuti assessor.js yang tidak memfilter tanggal,
    hanya mengambil N percakapan terbaru). Perbandingan tanggal memakai
    substr(tanggal,1,10) agar baris dengan komponen jam tidak ter-eksklusi
    pada hari terakhir rentang.
    """
    import datetime as _dtt
    today = _dtt.date.today()
    if range_ == "today":
        d_from = d_to = today.isoformat()
    elif range_ == "yesterday":
        y = today - _dtt.timedelta(days=1)
        d_from = d_to = y.isoformat()
    elif range_ == "7d":
        d_from = (today - _dtt.timedelta(days=6)).isoformat()
        d_to   = today.isoformat()
    elif range_ == "30d":
        d_from = (today - _dtt.timedelta(days=29)).isoformat()
        d_to   = today.isoformat()
    elif range_ == "90d":
        d_from = (today - _dtt.timedelta(days=89)).isoformat()
        d_to   = today.isoformat()
    elif range_ == "custom" and start and end:
        d_from, d_to = start, end
    else:
        d_from = d_to = None

    sql = ("SELECT sid,tanggal,customer,agent_name,agent_id,durasi,"
           "is_poro,jenis_layanan,"
           "ss_salam_pembuka,ss_menanyakan_nama,ss_menyapa_customer,"
           "ss_menawarkan_bantuan,ss_hold,ss_salam_penutup,ss_lengkap "
           "FROM awe_conversations WHERE 1=1")
    params = []
    if d_from:
        sql += " AND substr(tanggal,1,10)>=?"; params.append(d_from)
    if d_to:
        sql += " AND substr(tanggal,1,10)<=?"; params.append(d_to)
    if agent:
        sql += " AND agent_name LIKE ?"; params.append("%" + agent + "%")
    if poro == "ya":
        sql += " AND is_poro=1"
    elif poro == "tidak":
        sql += " AND is_poro=0"
    if jenis:
        sql += " AND jenis_layanan=?"; params.append(jenis)
    if ss_lengkap == "ya":
        sql += " AND ss_lengkap=1"
    elif ss_lengkap == "tidak":
        sql += " AND ss_lengkap=0"
    for attr, v in (ss_attrs or {}).items():
        col = "ss_" + attr
        sql += " AND " + col + "=?"
        params.append(1 if v == "ya" else 0)
    sql += " ORDER BY tanggal DESC,rowid DESC LIMIT ?"
    params.append(int(limit))

    rows = conn.execute(sql, params).fetchall()
    convs = [dict(r) for r in rows]

    agents  = [r[0] for r in conn.execute(
        "SELECT DISTINCT agent_name FROM awe_conversations "
        "WHERE agent_name!='' ORDER BY agent_name").fetchall()]
    jenises = [r[0] for r in conn.execute(
        "SELECT DISTINCT jenis_layanan FROM awe_conversations "
        "WHERE jenis_layanan IS NOT NULL AND jenis_layanan!='' "
        "ORDER BY jenis_layanan").fetchall()]
    return {"conversations": convs, "total": len(convs),
            "agents": agents, "jenises": jenises}


def delete_run(conn, run_id):
    cur = conn.cursor()
    cur.execute("DELETE FROM awe_conversations WHERE run_id=?", (run_id,))
    cur.execute("DELETE FROM awe_runs WHERE id=?", (run_id,))
    conn.commit()
    return cur.rowcount


def latest_run(conn, with_records=False):
    cur = conn.cursor()
    r = cur.execute(
        "SELECT id FROM awe_runs ORDER BY datetime(created_at) DESC LIMIT 1"
    ).fetchone()
    if not r:
        return None
    return get_run(conn, r["id"], with_records=with_records)


def stats(conn):
    cur = conn.cursor()
    row = cur.execute(
        """SELECT COUNT(*) AS runs, COALESCE(SUM(total_conv),0) AS conv,
                  MIN(date_min) AS dmin, MAX(date_max) AS dmax
           FROM awe_runs"""
    ).fetchone()
    return {"runs": row["runs"], "conversations": row["conv"],
            "date_min": row["dmin"] or "", "date_max": row["dmax"] or ""}


def set_meta(conn, key, value):
    conn.execute(
        "INSERT INTO awe_meta(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


def get_meta(conn, key, default=None):
    r = conn.execute("SELECT value FROM awe_meta WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


# =============================================================
# STAGING
# =============================================================
def _stage_day_of(c):
    return str(_g(c, "tanggal", "date", "start", default=""))[:10]


def stage_upsert_convs(conn, convs, batch_id=None, pulled_by=None):
    cur = conn.cursor()
    now = _jkt_now_iso()
    n_seen = n_new = 0
    for c in convs:
        if not isinstance(c, dict):
            continue
        sid = str(_g(c, "sid", "Sid", default="")).strip()
        if not sid:
            continue
        n_seen += 1
        r = cur.execute(
            "INSERT OR IGNORE INTO awe_staging"
            "(sid,tanggal,agent_id,agent_name,customer,durasi,payload_json,batch_id,pulled_by,pulled_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (sid, _stage_day_of(c),
             str(_g(c, "agentId", "agent_id", default="")),
             str(_g(c, "agentName", "agent", "agent_name", default="")),
             str(_g(c, "customer", "pelanggan", default="")),
             int(_g(c, "durasi", "duration", default=0) or 0),
             _json.dumps(c, ensure_ascii=False),
             batch_id or "", pulled_by or "", now),
        )
        if r.rowcount:
            n_new += 1
    conn.commit()
    return {"seen": n_seen, "new": n_new, "dup": n_seen - n_new}


def stage_add_batch(conn, batch_id, date_from, date_to, n_pulled, n_new, pulled_by=None):
    conn.execute(
        "INSERT OR REPLACE INTO awe_stage_batches"
        "(id,date_from,date_to,n_pulled,n_new,pulled_by,created_at) VALUES(?,?,?,?,?,?,?)",
        (batch_id, str(date_from)[:10], str(date_to)[:10], int(n_pulled or 0),
         int(n_new or 0), pulled_by or "", _jkt_now_iso()),
    )
    conn.commit()


def stage_mark_days(conn, convs, day_from=None, day_to=None, batch_id=None, pulled_by=None):
    now = _jkt_now_iso()
    by_day = {}
    for c in convs:
        if not isinstance(c, dict):
            continue
        d = _stage_day_of(c)
        if d:
            by_day[d] = by_day.get(d, 0) + 1
    days = sorted(by_day) or [d for d in _days_in_range(day_from, day_to) if d]
    cur = conn.cursor()
    for d in days:
        cur.execute(
            "INSERT INTO awe_stage_coverage(day,batch_id,total_conv,pulled_by,pulled_at)"
            " VALUES(?,?,?,?,?) ON CONFLICT(day) DO UPDATE SET"
            " batch_id=excluded.batch_id, total_conv=excluded.total_conv,"
            " pulled_by=excluded.pulled_by, pulled_at=excluded.pulled_at",
            (d, batch_id or "", by_day.get(d), pulled_by or "", now),
        )
    conn.commit()
    return days


def stage_coverage_for_range(conn, day_from, day_to):
    req = _days_in_range(day_from, day_to)
    cur = conn.cursor()
    have = set()
    if req:
        qs = ",".join("?" for _ in req)
        rs = cur.execute(
            "SELECT day FROM awe_stage_coverage WHERE day IN (%s)" % qs, req
        ).fetchall()
        have = set(r["day"] for r in rs)
    staged  = [d for d in req if d in have]
    missing = [d for d in req if d not in have]
    return {"requested": req, "staged": staged, "missing": missing}


def stage_stats(conn):
    cur = conn.cursor()
    row = cur.execute(
        "SELECT COUNT(*) AS n, MIN(tanggal) AS dmin, MAX(tanggal) AS dmax FROM awe_staging"
    ).fetchone()
    ndays = cur.execute("SELECT COUNT(*) FROM awe_stage_coverage").fetchone()[0]
    nb    = cur.execute("SELECT COUNT(*) FROM awe_stage_batches").fetchone()[0]
    return {"total": row["n"] or 0, "date_min": row["dmin"] or "",
            "date_max": row["dmax"] or "", "days": ndays, "batches": nb}


def stage_list_batches(conn, limit=100):
    rs = conn.execute(
        "SELECT id,date_from,date_to,n_pulled,n_new,pulled_by,created_at"
        " FROM awe_stage_batches ORDER BY datetime(created_at) DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    return [dict(r) for r in rs]


def stage_count(conn, day_from=None, day_to=None):
    if day_from and day_to:
        return conn.execute(
            "SELECT COUNT(*) FROM awe_staging WHERE tanggal>=? AND tanggal<=?",
            (str(day_from)[:10], str(day_to)[:10]),
        ).fetchone()[0]
    return conn.execute("SELECT COUNT(*) FROM awe_staging").fetchone()[0]


def stage_load_convs(conn, day_from=None, day_to=None):
    if day_from and day_to:
        rs = conn.execute(
            "SELECT payload_json FROM awe_staging WHERE tanggal>=? AND tanggal<=?"
            " ORDER BY tanggal, sid", (str(day_from)[:10], str(day_to)[:10]),
        ).fetchall()
    else:
        rs = conn.execute(
            "SELECT payload_json FROM awe_staging ORDER BY tanggal, sid"
        ).fetchall()
    out = []
    for r in rs:
        try:
            out.append(_json.loads(r["payload_json"]))
        except Exception:
            pass
    return out


def stage_purge(conn, day_from=None, day_to=None):
    cur = conn.cursor()
    if day_from and day_to:
        a, b = str(day_from)[:10], str(day_to)[:10]
        n = cur.execute("SELECT COUNT(*) FROM awe_staging WHERE tanggal>=? AND tanggal<=?",
                        (a, b)).fetchone()[0]
        cur.execute("DELETE FROM awe_staging WHERE tanggal>=? AND tanggal<=?", (a, b))
        cur.execute("DELETE FROM awe_stage_coverage WHERE day>=? AND day<=?", (a, b))
        cur.execute("DELETE FROM awe_stage_batches WHERE date_from>=? AND date_to<=?", (a, b))
    else:
        n = cur.execute("SELECT COUNT(*) FROM awe_staging").fetchone()[0]
        cur.execute("DELETE FROM awe_staging")
        cur.execute("DELETE FROM awe_stage_coverage")
        cur.execute("DELETE FROM awe_stage_batches")
    conn.commit()
    return n


if __name__ == "__main__":
    import tempfile
    p = os.path.join(tempfile.gettempdir(), "avaya_smoke.db")
    if os.path.exists(p): os.remove(p)
    c = init_db(connect(p))
    dash = {"meta": {"date_min": "2026-07-01", "date_max": "2026-07-31",
                      "total_conv": 2, "total_customers": 2, "engine": "mpnet"},
            "conversations": [
                {"sid": "A1", "tanggal": "2026-07-02 09:15:00", "customer": "Budi Santoso",
                 "mapped_intent": "Lapor SPT", "coverage_band": "Tinggi", "sentiment": "positif"},
                {"sid": "A2", "tanggal": "2026-07-05 14:00:00", "customer": "cust2",
                 "mapped_intent": "EFIN", "coverage_band": "Rendah", "sentiment": "negatif"},
            ]}
    records = [{
        "sid": "A1",
        "transkrip": [
            {"role": "customer", "text": "halo"},
            {"role": "agent",    "text": "Selamat pagi Bapak Budi, perkenalkan saya Rini. Ada yang bisa kami bantu?"},
            {"role": "customer", "text": "saya lupa EFIN"},
            {"role": "agent",    "text": "Mohon menunggu, kami cek terlebih dahulu."},
            {"role": "agent",    "text": "Terima kasih telah menggunakan layanan kami."},
        ],
    }]
    r = save_run(c, dash, records=records, n_files=1, source="upload", build="test")
    assert r["new"] is True and r["total_conv"] == 2, r
    tx = get_transcript(c, "A1")
    assert tx and tx["source"] == "database" and len(tx["transkrip"]) == 5, tx
    # softskill scores
    assert tx["softskill"]["salam_pembuka"] == 1, tx["softskill"]
    assert tx["softskill"]["menawarkan_bantuan"] == 1, tx["softskill"]
    assert tx["softskill"]["hold"] == 1, tx["softskill"]
    assert tx["softskill"]["salam_penutup"] == 1, tx["softskill"]
    # jenis layanan
    assert tx["jenis_layanan"] == "Lupa EFIN", tx["jenis_layanan"]
    # list_for_assess (default all)
    la = list_for_assess(c, range_="all")
    assert la["total"] == 2, la["total"]
    la2 = list_for_assess(c, range_="all", poro="tidak")
    assert la2["total"] >= 0
    # REGRESI: rentang custom hari-akhir tepat harus tetap menangkap baris
    # ber-timestamp (perbaikan substr(tanggal,1,10)).
    la3 = list_for_assess(c, range_="custom", start="2026-07-02", end="2026-07-02")
    assert la3["total"] == 1, ("substr date fix", la3["total"])
    la4 = list_for_assess(c, range_="custom", start="2026-07-01", end="2026-07-31")
    assert la4["total"] == 2, la4["total"]
    assert get_transcript(c, "NOPE") is None
    r2 = save_run(c, dash, records=[{"sid": "A1"}], n_files=1)
    assert r2["new"] is False and r2["id"] == r["id"], r2
    assert len(list_runs(c)) == 1
    st = stats(c)
    assert st["runs"] == 1 and st["conversations"] == 2, st
    assert delete_run(c, r["id"]) >= 1
    assert len(list_runs(c)) == 0
    print("AVAYA_DB_SMOKE_OK")
