# -*- coding: utf-8 -*-
"""sosmed_db.py — Fondasi data Tool Sosmed Kring Pajak (X / IG / TikTok).

Meniru pola avaya_db.py: hanya STDLIB (sqlite3, json, dll.), punya smoke test
bawaan (`python3 sosmed_db.py` -> cetak SOSMED_DB_SMOKE_OK), dan dipakai oleh
sosmed_routes.py + collector platform (sosmed_x.py, dst.).

Ruang lingkup Fase 1 (MVP):
  - Skema penyimpanan item sosmed + pairing pertanyaan/jawaban (Q&A).
  - Klasifikasi topik memakai ULANG taksonomi jenis layanan dari avaya_db
    (satu sumber kebenaran), plus deteksi sentimen sederhana berbasis leksikon.
  - Query untuk Inbox, Detail thread, Coverage & SLA, Analytics, dan
    kandidat FAQ/Deflection.
  - Ingest dari impor manual (CSV/JSON hasil ekspor) maupun dari collector API.

Catatan X API (free tier):
  Free tier X API v2 sangat terbatas (mayoritas hanya tulis/posting + info akun
  sendiri; baca mentions/replies/search umumnya butuh tier Basic+). Karena itu
  modul ini TIDAK bergantung pada endpoint berbayar: data bisa masuk lewat
  impor manual, dan collector (sosmed_x.py) dirancang "capability-aware" — ambil
  yang tersedia di tier saat ini, dan otomatis lebih lengkap saat tier dinaikkan.
"""
import os
import re
import json
import sqlite3
import datetime as _dt

try:
    import avaya_db as _avdb  # reuse taksonomi topik (jenis layanan)
except Exception:            # pragma: no cover - fallback bila belum ada
    _avdb = None

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

PLATFORMS = ("x", "ig", "tiktok")
ITEM_TYPES = ("pertanyaan", "balasan_resmi", "mention", "komentar", "dm", "quote")
STATUSES = ("belum_terjawab", "terjawab", "pending", "diabaikan")


def default_db_path():
    return os.environ.get("SOSMED_DB_FILE") or os.path.join(_BASE_DIR, "sosmed.db")


def official_handles():
    """Handle akun resmi (lowercase, tanpa @). Bisa diatur via env
    SOSMED_OFFICIAL_HANDLES (pisah koma)."""
    raw = os.environ.get("SOSMED_OFFICIAL_HANDLES", "")
    hs = [h.strip().lstrip("@").lower() for h in raw.split(",") if h.strip()]
    if not hs:
        hs = ["kring_pajak", "kringpajak", "pajakrepublik", "ditjenpajakri"]
    return set(hs)


def sla_minutes():
    try:
        return int(os.environ.get("SOSMED_SLA_MINUTES", "60"))
    except Exception:
        return 60


# ---------------------------------------------------------------------------
# Waktu
# ---------------------------------------------------------------------------
def _jkt_now():
    try:
        from zoneinfo import ZoneInfo
        return _dt.datetime.now(ZoneInfo("Asia/Jakarta"))
    except Exception:
        return _dt.datetime.utcnow() + _dt.timedelta(hours=7)


def _jkt_now_iso():
    return _jkt_now().strftime("%Y-%m-%d %H:%M:%S")


def _jkt_today():
    return _jkt_now().strftime("%Y-%m-%d")


def _parse_dt(s):
    """Parse berbagai format tanggal jadi datetime naive (UTC->drop tz). None bila gagal."""
    if not s:
        return None
    s = str(s).strip()
    if not s:
        return None
    v = s.replace("Z", "+00:00")
    try:
        d = _dt.datetime.fromisoformat(v)
        if d.tzinfo is not None:
            d = d.astimezone(_dt.timezone.utc).replace(tzinfo=None)
        return d
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                "%Y/%m/%d %H:%M:%S", "%d/%m/%Y %H:%M", "%d-%m-%Y"):
        try:
            return _dt.datetime.strptime(s[:len(fmt) + 4], fmt)
        except Exception:
            continue
    return None


def _iso(s):
    """Normalisasi ke 'YYYY-MM-DD HH:MM:SS' bila mungkin, else string apa adanya."""
    d = _parse_dt(s)
    return d.strftime("%Y-%m-%d %H:%M:%S") if d else (str(s).strip() if s else "")


# ---------------------------------------------------------------------------
# Koneksi & skema
# ---------------------------------------------------------------------------
def connect(db_path=None):
    conn = sqlite3.connect(db_path or default_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


_ITEM_COLS = [
    ("platform", "platform TEXT"),
    ("external_id", "external_id TEXT"),
    ("conversation_id", "conversation_id TEXT"),
    ("permalink", "permalink TEXT"),
    ("item_type", "item_type TEXT"),
    ("author_handle", "author_handle TEXT"),
    ("author_name", "author_name TEXT"),
    ("author_id", "author_id TEXT"),
    ("is_official", "is_official INTEGER DEFAULT 0"),
    ("created_at", "created_at TEXT"),
    ("text", "text TEXT"),
    ("language", "language TEXT"),
    ("in_reply_to_id", "in_reply_to_id TEXT"),
    ("topik", "topik TEXT"),
    ("sentiment", "sentiment TEXT"),
    ("status", "status TEXT DEFAULT 'belum_terjawab'"),
    ("answered_by", "answered_by TEXT"),
    ("answered_at", "answered_at TEXT"),
    ("response_time_s", "response_time_s INTEGER"),
    ("like_count", "like_count INTEGER DEFAULT 0"),
    ("reply_count", "reply_count INTEGER DEFAULT 0"),
    ("repost_count", "repost_count INTEGER DEFAULT 0"),
    ("hashtags", "hashtags TEXT"),
    ("raw_json", "raw_json TEXT"),
    ("batch_id", "batch_id TEXT"),
    ("fetched_at", "fetched_at TEXT"),
]


def _ensure_columns(cur, table, coldefs):
    have = {r[1] for r in cur.execute('PRAGMA table_info("%s")' % table).fetchall()}
    for name, ddl in coldefs:
        if name not in have:
            cur.execute('ALTER TABLE "%s" ADD COLUMN %s' % (table, ddl))


def init_db(conn):
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sosmed_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT,
            external_id TEXT,
            conversation_id TEXT,
            permalink TEXT,
            item_type TEXT,
            author_handle TEXT,
            author_name TEXT,
            author_id TEXT,
            is_official INTEGER DEFAULT 0,
            created_at TEXT,
            text TEXT,
            language TEXT,
            in_reply_to_id TEXT,
            topik TEXT,
            sentiment TEXT,
            status TEXT DEFAULT 'belum_terjawab',
            answered_by TEXT,
            answered_at TEXT,
            response_time_s INTEGER,
            like_count INTEGER DEFAULT 0,
            reply_count INTEGER DEFAULT 0,
            repost_count INTEGER DEFAULT 0,
            hashtags TEXT,
            raw_json TEXT,
            batch_id TEXT,
            fetched_at TEXT
        )
    """)
    _ensure_columns(cur, "sosmed_items", _ITEM_COLS)
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_sm_ext "
                "ON sosmed_items(platform, external_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_sm_conv "
                "ON sosmed_items(platform, conversation_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_sm_created ON sosmed_items(created_at)")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sosmed_batches (
            batch_id TEXT PRIMARY KEY,
            platform TEXT,
            source TEXT,
            date_from TEXT,
            date_to TEXT,
            n_fetched INTEGER,
            n_new INTEGER,
            pulled_by TEXT,
            created_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sosmed_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------
def set_meta(conn, key, value):
    conn.execute("INSERT INTO sosmed_meta(key,value) VALUES(?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, str(value)))
    conn.commit()


def get_meta(conn, key, default=None):
    r = conn.execute("SELECT value FROM sosmed_meta WHERE key=?", (key,)).fetchone()
    return r[0] if r else default


# ---------------------------------------------------------------------------
# Klasifikasi: topik (reuse avaya_db) + sentimen
# ---------------------------------------------------------------------------
def classify_topik(text):
    """Deteksi jenis layanan/topik memakai taksonomi avaya_db (satu sumber)."""
    if not text:
        return None
    if _avdb is not None and hasattr(_avdb, "_detect_layanan"):
        try:
            return _avdb._detect_layanan(text)
        except Exception:
            return None
    return None


_SENT_NEG = (
    "marah", "kecewa", "lambat", "lama banget", "lelet", "ribet", "susah",
    "error", "gagal", "komplain", "keluhan", "buruk", "jelek", "parah",
    "kesal", "kesel", "protes", "bermasalah", "dipersulit", "mengecewakan",
    "tidak bisa", "ga bisa", "gabisa", "nggak bisa", "gak bisa", "belum juga",
    "payah", "lambannya", "dikacangin", "gak jelas", "tidak jelas",
)
_SENT_POS = (
    "terima kasih", "terimakasih", "makasih", "makasi", "mantap", "keren",
    "cepat", "membantu", "bagus", "puas", "solutif", "ramah", "baik",
    "sip", "oke", "alhamdulillah", "terbantu", "responsif", "jelas sekali",
)


def detect_sentiment(text):
    if not text:
        return "netral"
    t = " " + text.lower() + " "
    neg = sum(1 for w in _SENT_NEG if w in t)
    pos = sum(1 for w in _SENT_POS if w in t)
    if neg > pos:
        return "negatif"
    if pos > neg:
        return "positif"
    return "netral"


_HASHTAG_RE = re.compile(r"#\w+", re.UNICODE)


def extract_hashtags(text):
    if not text:
        return []
    return [h.lower() for h in _HASHTAG_RE.findall(text)]


# ---------------------------------------------------------------------------
# Normalisasi item masuk (fleksibel terhadap nama field)
# ---------------------------------------------------------------------------
def _g(d, *keys, default=""):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


def _norm_platform(p):
    p = (str(p or "").strip().lower())
    if p in ("twitter", "x.com", "x"):
        return "x"
    if p in ("instagram", "ig", "insta"):
        return "ig"
    if p in ("tiktok", "tik tok", "tt"):
        return "tiktok"
    return p or "x"


def _norm_item(raw, default_platform=None):
    """Ubah dict mentah (dari ekspor/API) jadi baris ternormalisasi."""
    d = raw if isinstance(raw, dict) else {}
    platform = _norm_platform(_g(d, "platform", default=default_platform or "x"))
    ext = str(_g(d, "external_id", "id", "id_str", "tweet_id", "comment_id", default="")).strip()
    conv = str(_g(d, "conversation_id", "conv_id", "thread_id", default="")).strip()
    reply_to = str(_g(d, "in_reply_to_id", "in_reply_to_status_id", "parent_id",
                      "reply_to", default="")).strip()
    handle = str(_g(d, "author_handle", "username", "handle", "screen_name",
                    "from", default="")).strip().lstrip("@")
    name = str(_g(d, "author_name", "name", "display_name", default=handle)).strip()
    author_id = str(_g(d, "author_id", "user_id", default="")).strip()
    text = str(_g(d, "text", "body", "message", "content", "comment", default="")).strip()
    created = _iso(_g(d, "created_at", "created", "date", "timestamp", "time", default=""))
    lang = str(_g(d, "language", "lang", default="")).strip().lower()
    permalink = str(_g(d, "permalink", "url", "link", default="")).strip()

    off_set = official_handles()
    is_official = 1 if (str(_g(d, "is_official", default="")).lower() in ("1", "true", "ya", "yes")
                        or (handle and handle.lower() in off_set)) else 0

    itype = str(_g(d, "item_type", "type", default="")).strip().lower()
    if itype not in ITEM_TYPES:
        if is_official and reply_to:
            itype = "balasan_resmi"
        elif reply_to:
            itype = "pertanyaan"
        else:
            itype = "mention" if platform == "x" else "komentar"

    def _int(*keys):
        try:
            return int(float(_g(d, *keys, default=0)))
        except Exception:
            return 0

    if not conv:
        conv = ext  # fallback: item berdiri sendiri sebagai thread-nya sendiri

    return {
        "platform": platform,
        "external_id": ext,
        "conversation_id": conv,
        "permalink": permalink,
        "item_type": itype,
        "author_handle": handle,
        "author_name": name,
        "author_id": author_id,
        "is_official": is_official,
        "created_at": created,
        "text": text,
        "language": lang or "id",
        "in_reply_to_id": reply_to,
        "topik": classify_topik(text),
        "sentiment": detect_sentiment(text),
        "like_count": _int("like_count", "likes", "favorite_count"),
        "reply_count": _int("reply_count", "replies"),
        "repost_count": _int("repost_count", "retweet_count", "shares"),
        "hashtags": json.dumps(extract_hashtags(text), ensure_ascii=False),
        "raw_json": json.dumps(d, ensure_ascii=False),
    }


# ---------------------------------------------------------------------------
# Ingest + pairing Q&A
# ---------------------------------------------------------------------------
def ingest_items(conn, items, default_platform=None, source="import",
                 batch_id=None, pulled_by=None):
    """Masukkan daftar item mentah. Upsert by (platform, external_id).
    Mengembalikan {ok, n_in, n_new, n_dup, n_skip, batch, platforms, convs}."""
    import uuid as _uuid
    if batch_id is None:
        batch_id = _uuid.uuid4().hex[:12]
    now = _jkt_now_iso()
    cur = conn.cursor()
    n_new = n_dup = n_skip = 0
    touched_convs = {}   # (platform, conv) -> True
    platforms = set()
    for raw in (items or []):
        row = _norm_item(raw, default_platform=default_platform)
        if not row["external_id"] or not row["text"]:
            n_skip += 1
            continue
        platforms.add(row["platform"])
        touched_convs[(row["platform"], row["conversation_id"])] = True
        exists = cur.execute(
            "SELECT id FROM sosmed_items WHERE platform=? AND external_id=?",
            (row["platform"], row["external_id"])).fetchone()
        cols = list(row.keys()) + ["batch_id", "fetched_at"]
        vals = [row[c] for c in row.keys()] + [batch_id, now]
        if exists:
            # update konten & metrik (jangan timpa status pairing di sini)
            setcols = [c for c in row.keys()
                       if c not in ("platform", "external_id", "status")]
            cur.execute(
                "UPDATE sosmed_items SET %s WHERE platform=? AND external_id=?"
                % ", ".join("%s=?" % c for c in setcols),
                [row[c] for c in setcols] + [row["platform"], row["external_id"]])
            n_dup += 1
        else:
            cur.execute(
                "INSERT INTO sosmed_items(%s) VALUES(%s)"
                % (",".join(cols), ",".join("?" * len(cols))), vals)
            n_new += 1
    conn.commit()

    # Pairing Q&A untuk conversation yang tersentuh
    for (plat, conv) in touched_convs.keys():
        _pair_conversation(conn, plat, conv)
    conn.commit()

    # Catat batch
    days = [r[0][:10] for r in cur.execute(
        "SELECT created_at FROM sosmed_items WHERE batch_id=? AND created_at!=''",
        (batch_id,)).fetchall() if r[0]]
    d_from = min(days) if days else ""
    d_to = max(days) if days else ""
    conn.execute(
        "INSERT OR REPLACE INTO sosmed_batches"
        "(batch_id,platform,source,date_from,date_to,n_fetched,n_new,pulled_by,created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (batch_id, ",".join(sorted(platforms)), source, d_from, d_to,
         n_new + n_dup, n_new, pulled_by or "", now))
    set_meta(conn, "last_ingest_at", now)
    conn.commit()
    return {"ok": True, "n_in": len(items or []), "n_new": n_new, "n_dup": n_dup,
            "n_skip": n_skip, "batch": batch_id,
            "platforms": sorted(platforms), "convs": len(touched_convs)}


def _pair_conversation(conn, platform, conv):
    """Pasangkan pertanyaan customer dgn balasan resmi dalam satu conversation.
    Set status/answered_by/answered_at/response_time_s pada item pertanyaan."""
    rows = conn.execute(
        "SELECT id,external_id,in_reply_to_id,is_official,created_at,author_handle,status "
        "FROM sosmed_items WHERE platform=? AND conversation_id=?",
        (platform, conv)).fetchall()
    if not rows:
        return
    by_ext = {r["external_id"]: r for r in rows if r["external_id"]}
    officials = [r for r in rows if r["is_official"] == 1]

    # Untuk tiap balasan resmi yg membalas item tertentu -> tandai item itu terjawab
    answered = {}  # question_ext -> (official_row)
    for off in officials:
        tgt = off["in_reply_to_id"]
        if tgt and tgt in by_ext and by_ext[tgt]["is_official"] != 1:
            prev = answered.get(tgt)
            # ambil balasan resmi paling awal
            if prev is None or _lt(off["created_at"], prev["created_at"]):
                answered[tgt] = off

    for r in rows:
        if r["is_official"] == 1:
            continue
        if r["status"] == "diabaikan":
            continue  # keputusan manual dihormati
        off = answered.get(r["external_id"])
        if off is not None:
            rt = _resp_seconds(r["created_at"], off["created_at"])
            conn.execute(
                "UPDATE sosmed_items SET status='terjawab',answered_by=?,answered_at=?,"
                "response_time_s=? WHERE id=?",
                (off["author_handle"], off["created_at"], rt, r["id"]))
        else:
            if r["status"] not in ("pending",):
                conn.execute("UPDATE sosmed_items SET status='belum_terjawab' WHERE id=?",
                             (r["id"],))


def _lt(a, b):
    da, db = _parse_dt(a), _parse_dt(b)
    if da and db:
        return da < db
    return str(a) < str(b)


def _resp_seconds(q_created, a_created):
    dq, da = _parse_dt(q_created), _parse_dt(a_created)
    if dq and da:
        return max(0, int((da - dq).total_seconds()))
    return None


def set_status(conn, item_id, status):
    if status not in STATUSES:
        return False
    conn.execute("UPDATE sosmed_items SET status=? WHERE id=?", (status, item_id))
    conn.commit()
    return True


def set_topik(conn, item_id, topik):
    conn.execute("UPDATE sosmed_items SET topik=? WHERE id=?", (topik or None, item_id))
    conn.commit()
    return True


# ---------------------------------------------------------------------------
# Rentang tanggal
# ---------------------------------------------------------------------------
def resolve_range(preset):
    preset = (preset or "").strip().lower()
    today = _jkt_now().date()
    if preset in ("", "all"):
        return None, None
    if preset == "today":
        return today.isoformat(), today.isoformat()
    if preset == "yesterday":
        y = today - _dt.timedelta(days=1)
        return y.isoformat(), y.isoformat()
    m = {"7d": 7, "30d": 30, "90d": 90}
    if preset in m:
        return (today - _dt.timedelta(days=m[preset] - 1)).isoformat(), today.isoformat()
    return None, None


# ---------------------------------------------------------------------------
# Query: Inbox / Daftar Q&A
# ---------------------------------------------------------------------------
def list_items(conn, platform="", range_="all", start="", end="", topik="",
               status="", sentiment="", item_type="", handle="", q="",
               only_questions=False, limit=200):
    where = ["1=1"]
    params = []
    if platform:
        where.append("platform=?"); params.append(_norm_platform(platform))
    rng = (range_ or "all").lower()
    if rng == "custom":
        s, e = (start or None), (end or start or None)
    else:
        s, e = resolve_range(rng)
    if s:
        where.append("substr(created_at,1,10)>=?"); params.append(s)
    if e:
        where.append("substr(created_at,1,10)<=?"); params.append(e)
    if topik:
        where.append("topik=?"); params.append(topik)
    if status:
        where.append("status=?"); params.append(status)
    if sentiment:
        where.append("sentiment=?"); params.append(sentiment)
    if item_type:
        where.append("item_type=?"); params.append(item_type)
    if handle:
        where.append("lower(author_handle) LIKE ?"); params.append("%" + handle.lower() + "%")
    if q:
        where.append("(text LIKE ? OR author_name LIKE ? OR author_handle LIKE ?)")
        params += ["%" + q + "%"] * 3
    if only_questions:
        where.append("is_official=0")
    sql = ("SELECT * FROM sosmed_items WHERE " + " AND ".join(where)
           + " ORDER BY datetime(created_at) DESC LIMIT ?")
    params.append(int(limit))
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

    platforms = [r[0] for r in conn.execute(
        "SELECT DISTINCT platform FROM sosmed_items WHERE platform!='' "
        "ORDER BY platform").fetchall()]
    topiks = [r[0] for r in conn.execute(
        "SELECT DISTINCT topik FROM sosmed_items WHERE topik IS NOT NULL AND topik!='' "
        "ORDER BY topik").fetchall()]
    handles = [r[0] for r in conn.execute(
        "SELECT DISTINCT author_handle FROM sosmed_items "
        "WHERE author_handle!='' ORDER BY author_handle LIMIT 500").fetchall()]
    return {"ok": True, "items": rows, "total": len(rows),
            "platforms": platforms, "topiks": topiks, "handles": handles}


def get_thread(conn, platform, conversation_id):
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM sosmed_items WHERE platform=? AND conversation_id=? "
        "ORDER BY datetime(created_at) ASC, id ASC",
        (_norm_platform(platform), conversation_id)).fetchall()]
    if not rows:
        return None
    return {"ok": True, "platform": _norm_platform(platform),
            "conversation_id": conversation_id, "items": rows, "n": len(rows)}


def get_item(conn, item_id):
    r = conn.execute("SELECT * FROM sosmed_items WHERE id=?", (item_id,)).fetchone()
    return dict(r) if r else None


# ---------------------------------------------------------------------------
# Coverage & SLA
# ---------------------------------------------------------------------------
def coverage_sla(conn, platform="", range_="all", start="", end=""):
    where = ["is_official=0"]
    params = []
    if platform:
        where.append("platform=?"); params.append(_norm_platform(platform))
    rng = (range_ or "all").lower()
    s, e = ((start or None), (end or start or None)) if rng == "custom" else resolve_range(rng)
    if s:
        where.append("substr(created_at,1,10)>=?"); params.append(s)
    if e:
        where.append("substr(created_at,1,10)<=?"); params.append(e)
    w = " AND ".join(where)
    total = conn.execute("SELECT COUNT(*) FROM sosmed_items WHERE " + w, params).fetchone()[0]
    answered = conn.execute(
        "SELECT COUNT(*) FROM sosmed_items WHERE " + w + " AND status='terjawab'",
        params).fetchone()[0]
    unanswered = conn.execute(
        "SELECT COUNT(*) FROM sosmed_items WHERE " + w + " AND status='belum_terjawab'",
        params).fetchone()[0]
    ignored = conn.execute(
        "SELECT COUNT(*) FROM sosmed_items WHERE " + w + " AND status='diabaikan'",
        params).fetchone()[0]
    rts = [r[0] for r in conn.execute(
        "SELECT response_time_s FROM sosmed_items WHERE " + w
        + " AND response_time_s IS NOT NULL", params).fetchall()]
    avg_rt = round(sum(rts) / len(rts)) if rts else None
    med_rt = _median(rts)
    sla_s = sla_minutes() * 60
    within = sum(1 for x in rts if x <= sla_s)
    breach = sum(1 for x in rts if x > sla_s)
    pct_answered = round(100.0 * answered / total, 1) if total else 0.0
    pct_sla = round(100.0 * within / len(rts), 1) if rts else 0.0
    return {"ok": True, "total": total, "answered": answered,
            "unanswered": unanswered, "ignored": ignored,
            "pct_answered": pct_answered, "avg_response_s": avg_rt,
            "median_response_s": med_rt, "sla_minutes": sla_minutes(),
            "within_sla": within, "breach_sla": breach, "pct_within_sla": pct_sla}


def _median(xs):
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else round((s[n // 2 - 1] + s[n // 2]) / 2)


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------
def analytics(conn, platform="", range_="all", start="", end=""):
    where = ["1=1"]
    params = []
    if platform:
        where.append("platform=?"); params.append(_norm_platform(platform))
    rng = (range_ or "all").lower()
    s, e = ((start or None), (end or start or None)) if rng == "custom" else resolve_range(rng)
    if s:
        where.append("substr(created_at,1,10)>=?"); params.append(s)
    if e:
        where.append("substr(created_at,1,10)<=?"); params.append(e)
    w = " AND ".join(where)
    by_day = [{"day": r[0], "n": r[1]} for r in conn.execute(
        "SELECT substr(created_at,1,10) d, COUNT(*) FROM sosmed_items WHERE " + w
        + " AND created_at!='' GROUP BY d ORDER BY d", params).fetchall()]
    by_topik = [{"topik": r[0] or "(tidak terklasifikasi)", "n": r[1]} for r in conn.execute(
        "SELECT topik, COUNT(*) c FROM sosmed_items WHERE " + w
        + " AND is_official=0 GROUP BY topik ORDER BY c DESC", params).fetchall()]
    by_sent = [{"sentiment": r[0] or "netral", "n": r[1]} for r in conn.execute(
        "SELECT sentiment, COUNT(*) c FROM sosmed_items WHERE " + w
        + " AND is_official=0 GROUP BY sentiment ORDER BY c DESC", params).fetchall()]
    by_platform = [{"platform": r[0], "n": r[1]} for r in conn.execute(
        "SELECT platform, COUNT(*) c FROM sosmed_items WHERE " + w
        + " GROUP BY platform ORDER BY c DESC", params).fetchall()]
    return {"ok": True, "by_day": by_day, "by_topik": by_topik,
            "by_sentiment": by_sent, "by_platform": by_platform}


# ---------------------------------------------------------------------------
# FAQ / Deflection insight (kandidat FAQ dari pertanyaan berulang)
# ---------------------------------------------------------------------------
_STOP = set("""yang dan di ke dari untuk pada dengan atau ini itu ada apa
bagaimana gimana kenapa mengapa kah min admin kak pak bu mohon tolong ya
nya saya aku kami kita mau ingin bisa tidak gak ga nggak sudah belum juga
kalau jika saja lagi kok dong sih the a an is to of for""".split())


def _keywords(text, k=6):
    if not text:
        return []
    words = re.findall(r"[a-zA-Z]{3,}", text.lower())
    freq = {}
    for w in words:
        if w in _STOP:
            continue
        freq[w] = freq.get(w, 0) + 1
    return [w for w, _ in sorted(freq.items(), key=lambda x: -x[1])[:k]]


def faq_candidates(conn, platform="", range_="all", start="", end="",
                   only_unanswered=False, min_count=2, limit=50):
    """Klaster pertanyaan customer per topik -> kandidat FAQ.
    Fokus KPI: pertanyaan berulang & yang belum terjawab (deflection gap)."""
    where = ["is_official=0"]
    params = []
    if platform:
        where.append("platform=?"); params.append(_norm_platform(platform))
    rng = (range_ or "all").lower()
    s, e = ((start or None), (end or start or None)) if rng == "custom" else resolve_range(rng)
    if s:
        where.append("substr(created_at,1,10)>=?"); params.append(s)
    if e:
        where.append("substr(created_at,1,10)<=?"); params.append(e)
    if only_unanswered:
        where.append("status='belum_terjawab'")
    w = " AND ".join(where)
    rows = conn.execute(
        "SELECT topik,text,status,response_time_s,external_id,answered_by "
        "FROM sosmed_items WHERE " + w, params).fetchall()
    clusters = {}
    for r in rows:
        key = r["topik"] or "(lainnya)"
        c = clusters.setdefault(key, {"topik": key, "count": 0, "unanswered": 0,
                                      "samples": [], "kw": {}, "rts": []})
        c["count"] += 1
        if r["status"] == "belum_terjawab":
            c["unanswered"] += 1
        if r["response_time_s"] is not None:
            c["rts"].append(r["response_time_s"])
        if len(c["samples"]) < 3 and r["text"]:
            c["samples"].append(r["text"][:220])
        for kw in _keywords(r["text"]):
            c["kw"][kw] = c["kw"].get(kw, 0) + 1
    out = []
    for c in clusters.values():
        if c["count"] < min_count:
            continue
        top_kw = [k for k, _ in sorted(c["kw"].items(), key=lambda x: -x[1])[:8]]
        out.append({
            "topik": c["topik"], "count": c["count"],
            "unanswered": c["unanswered"],
            "deflection_gap": round(100.0 * c["unanswered"] / c["count"], 1),
            "avg_response_s": round(sum(c["rts"]) / len(c["rts"])) if c["rts"] else None,
            "keywords": top_kw, "samples": c["samples"],
        })
    # prioritas: paling sering & gap terbesar
    out.sort(key=lambda x: (x["count"], x["unanswered"]), reverse=True)
    return {"ok": True, "clusters": out[:limit],
            "sla_minutes": sla_minutes()}


# ---------------------------------------------------------------------------
# Batches / stats / housekeeping
# ---------------------------------------------------------------------------
def list_batches(conn, limit=100):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM sosmed_batches ORDER BY created_at DESC LIMIT ?",
        (limit,)).fetchall()]


def stats(conn):
    total = conn.execute("SELECT COUNT(*) FROM sosmed_items").fetchone()[0]
    q = conn.execute("SELECT COUNT(*) FROM sosmed_items WHERE is_official=0").fetchone()[0]
    ans = conn.execute("SELECT COUNT(*) FROM sosmed_items WHERE is_official=0 "
                       "AND status='terjawab'").fetchone()[0]
    plats = [dict(platform=r[0], n=r[1]) for r in conn.execute(
        "SELECT platform, COUNT(*) FROM sosmed_items GROUP BY platform").fetchall()]
    return {"total": total, "questions": q, "answered": ans,
            "platforms": plats, "batches": len(list_batches(conn)),
            "last_ingest": get_meta(conn, "last_ingest_at")}


def purge_all(conn):
    conn.execute("DELETE FROM sosmed_items")
    conn.execute("DELETE FROM sosmed_batches")
    conn.commit()


# ===========================================================================
# Smoke test (stdlib) — jalankan: python3 sosmed_db.py
# ===========================================================================
if __name__ == "__main__":
    import tempfile
    dbf = os.path.join(tempfile.mkdtemp(), "sosmed_test.db")
    os.environ["SOSMED_DB_FILE"] = dbf
    os.environ["SOSMED_OFFICIAL_HANDLES"] = "kring_pajak"
    c = init_db(connect())

    sample = [
        {"platform": "x", "id": "1001", "conversation_id": "c1",
         "author_handle": "wajibpajak", "created_at": "2026-08-04T10:00:00Z",
         "text": "min saya lupa EFIN gimana cara resetnya? ribet banget"},
        {"platform": "x", "id": "1002", "conversation_id": "c1",
         "in_reply_to_id": "1001", "author_handle": "kring_pajak",
         "created_at": "2026-08-04T10:25:00Z",
         "text": "Halo, silakan hubungi Kring Pajak 1500200 untuk reset EFIN. Terima kasih"},
        {"platform": "x", "id": "1003", "conversation_id": "c2",
         "author_handle": "orangpajak", "created_at": "2026-08-04T11:00:00Z",
         "text": "cara ganti email yang terdaftar di akun pajak dong min"},
        {"platform": "x", "id": "1004", "conversation_id": "c3",
         "author_handle": "marahwp", "created_at": "2026-08-04T12:00:00Z",
         "text": "lapor SPT tahunan error terus, kecewa banget lama"},
        {"platform": "ig", "id": "ig1", "conversation_id": "cig",
         "author_handle": "netizen", "created_at": "2026-08-05T09:00:00Z",
         "text": "lupa efin lagi nih, tolong bantu"},
    ]
    r = ingest_items(c, sample, source="import")
    assert r["ok"] and r["n_new"] == 5, r
    assert set(r["platforms"]) == {"x", "ig"}, r

    # klasifikasi topik
    it = get_item(c, 1)
    assert it["topik"] == "Lupa EFIN", it["topik"]
    assert it["sentiment"] == "negatif", it["sentiment"]

    # pairing Q&A: item 1001 terjawab oleh kring_pajak dalam 25 menit
    assert it["status"] == "terjawab", it["status"]
    assert it["answered_by"] == "kring_pajak", it["answered_by"]
    assert it["response_time_s"] == 25 * 60, it["response_time_s"]

    # ganti email -> Perubahan Data, belum terjawab
    it3 = get_item(c, 3)
    assert it3["topik"] == "Perubahan Data", it3["topik"]
    assert it3["status"] == "belum_terjawab", it3["status"]

    # inbox filter
    lst = list_items(c, only_questions=True)
    assert lst["total"] == 4, lst["total"]  # 4 pertanyaan customer (bukan balasan resmi)
    lst_x = list_items(c, platform="x", topik="Lupa EFIN", only_questions=True)
    assert lst_x["total"] == 1, lst_x["total"]

    # thread
    th = get_thread(c, "x", "c1")
    assert th and th["n"] == 2, th

    # coverage & SLA
    cov = coverage_sla(c)
    assert cov["total"] == 4 and cov["answered"] == 1, cov
    assert cov["within_sla"] == 1, cov  # 25 mnt <= 60 mnt

    # analytics
    an = analytics(c)
    topik_names = {x["topik"] for x in an["by_topik"]}
    assert "Lupa EFIN" in topik_names, an["by_topik"]

    # FAQ candidates: Lupa EFIN muncul 2x (x + ig)
    faq = faq_candidates(c, min_count=2)
    efin = [x for x in faq["clusters"] if x["topik"] == "Lupa EFIN"]
    assert efin and efin[0]["count"] == 2, faq["clusters"]

    # idempotensi: ingest ulang tidak menambah baris
    r2 = ingest_items(c, sample, source="import")
    assert r2["n_new"] == 0 and r2["n_dup"] == 5, r2

    st = stats(c)
    assert st["total"] == 5 and st["questions"] == 4 and st["answered"] == 1, st

    c.close()
    print("SOSMED_DB_SMOKE_OK")
