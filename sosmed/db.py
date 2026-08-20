# -*- coding: utf-8 -*-
"""sosmed_db.py — Fondasi data Tool Sosmed Kring Pajak (X / IG / TikTok).

Meniru pola avaya_db.py: hanya STDLIB (sqlite3, json, dll.), punya smoke test
bawaan (`python3 sosmed_db.py` -> cetak SOSMED_DB_SMOKE_OK), dan dipakai oleh
sosmed_routes.py + collector platform (sosmed_x.py, dst.).

Fokus rework (Agustus 2026): perbaikan "merajut" Q&A yang sadar-thread.
  Versi lama menyalahi thread berbalas-balas: pertanyaan ASLI (root non-resmi)
  ter-tag 'mention', dan balasan SUSULAN customer (mis. menjawab klarifikasi
  admin) ikut tercatat sebagai 'pertanyaan' baru. Versi ini merekonstruksi
  pohon balasan per conversation lalu:
    - Root non-resmi / non-resmi yang membalas sesama customer -> 'pertanyaan'.
    - Non-resmi yang membalas akun RESMI -> 'susulan' (bukan pertanyaan baru).
    - Semua item akun resmi -> 'balasan_resmi'.
    - Sebuah 'pertanyaan' berstatus 'terjawab' bila ADA balasan resmi di mana
      pun DI BAWAHNYA pada pohon balasan (bukan sekadar in_reply_to langsung),
      sehingga admin yang membalas node berbeda tetap terhitung menjawab.

Klasifikasi topik memakai ULANG taksonomi jenis layanan dari avaya_db (satu
sumber kebenaran); sentimen sederhana berbasis leksikon. Semua query menyaring
pertanyaan lewat item_type='pertanyaan' (bukan sekadar is_official=0) agar
susulan/komentar tidak mengotori Coverage/SLA/FAQ.
"""
import os
import re
import json
import sqlite3
import datetime as _dt

try:
    import avaya.db as _avdb  # reuse taksonomi topik (jenis layanan)
except Exception:            # pragma: no cover - fallback bila belum ada
    _avdb = None

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PLATFORMS = ("x", "ig", "tiktok")
# 'susulan' = balasan lanjutan customer kepada akun resmi (bukan pertanyaan baru).
ITEM_TYPES = ("pertanyaan", "balasan_resmi", "susulan", "mention",
              "komentar", "dm", "quote")
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
    ("answer_text", "answer_text TEXT"),
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
            answer_text TEXT,
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
    cur.execute("CREATE INDEX IF NOT EXISTS ix_sm_type ON sosmed_items(item_type)")
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
    """Ubah dict mentah (dari ekspor/API) jadi baris ternormalisasi.

    CATATAN: item_type di sini hanya PROVISIONAL. Nilai final ditetapkan
    _pair_conversation() yang melihat seluruh pohon balasan (lihat modul docstring).
    """
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

    # item_type provisional (bisa dari sumber; kalau tidak, tebakan awal).
    itype = str(_g(d, "item_type", "type", default="")).strip().lower()
    if itype not in ITEM_TYPES:
        itype = "balasan_resmi" if is_official else "pertanyaan"

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
            # update konten & metrik (jangan timpa status/pairing di sini;
            # item_type & status final diset oleh _pair_conversation).
            setcols = [c for c in row.keys()
                       if c not in ("platform", "external_id", "status", "item_type")]
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

    # Pairing Q&A (sadar-thread) untuk conversation yang tersentuh
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
    """Rekonstruksi pohon balasan satu conversation lalu tetapkan item_type &
    status Q&A yang benar (lihat modul docstring).

    Aturan item_type:
      - is_official                              -> 'balasan_resmi'
      - non-resmi membalas item RESMI            -> 'susulan'
      - selain itu (root / balas sesama customer)-> 'pertanyaan'
    Aturan status untuk 'pertanyaan':
      - 'terjawab' bila ADA balasan resmi sebagai keturunan pada pohon balasan
        (menelusuri in_reply_to ke atas dari tiap balasan resmi). Ambil balasan
        resmi paling awal sebagai jawaban (answered_by/at, answer_text, RT).
      - status 'diabaikan' (keputusan manual) dihormati, tidak ditimpa.
    """
    rows = conn.execute(
        "SELECT id,external_id,in_reply_to_id,is_official,created_at,author_handle,"
        "text,item_type,status FROM sosmed_items "
        "WHERE platform=? AND conversation_id=?",
        (platform, conv)).fetchall()
    if not rows:
        return
    by_ext = {r["external_id"]: r for r in rows if r["external_id"]}

    def _final_type(r):
        if r["is_official"] == 1:
            return "balasan_resmi"
        tgt = by_ext.get(r["in_reply_to_id"] or "")
        if r["in_reply_to_id"] and tgt is not None and tgt["is_official"] == 1:
            return "susulan"
        return "pertanyaan"

    # Jawaban: telusuri ancestor dari tiap balasan resmi; tandai pertanyaan
    # leluhur sebagai terjawab oleh balasan resmi paling awal.
    answer_of = {}   # question_ext -> official row
    for off in [r for r in rows if r["is_official"] == 1]:
        cur_node = by_ext.get(off["in_reply_to_id"] or "")
        seen = set()
        while cur_node is not None and cur_node["external_id"] not in seen:
            seen.add(cur_node["external_id"])
            if cur_node["is_official"] != 1 and _final_type(cur_node) == "pertanyaan":
                prev = answer_of.get(cur_node["external_id"])
                if prev is None or _lt(off["created_at"], prev["created_at"]):
                    answer_of[cur_node["external_id"]] = off
            cur_node = by_ext.get(cur_node["in_reply_to_id"] or "")

    for r in rows:
        ftype = _final_type(r)
        if r["is_official"] == 1:
            conn.execute(
                "UPDATE sosmed_items SET item_type='balasan_resmi', status='terjawab' "
                "WHERE id=?", (r["id"],))
            continue
        if ftype == "susulan":
            # bukan pertanyaan; bersihkan jejak pairing lama bila ada.
            conn.execute(
                "UPDATE sosmed_items SET item_type='susulan', status='pending',"
                "answered_by=NULL,answered_at=NULL,answer_text=NULL,response_time_s=NULL "
                "WHERE id=?", (r["id"],))
            continue
        # pertanyaan
        if r["status"] == "diabaikan":
            conn.execute("UPDATE sosmed_items SET item_type='pertanyaan' WHERE id=?",
                         (r["id"],))
            continue
        off = answer_of.get(r["external_id"])
        if off is not None:
            rt = _resp_seconds(r["created_at"], off["created_at"])
            conn.execute(
                "UPDATE sosmed_items SET item_type='pertanyaan',status='terjawab',"
                "answered_by=?,answered_at=?,answer_text=?,response_time_s=? WHERE id=?",
                (off["author_handle"], off["created_at"], off["text"], rt, r["id"]))
        else:
            conn.execute(
                "UPDATE sosmed_items SET item_type='pertanyaan',status='belum_terjawab',"
                "answered_by=NULL,answered_at=NULL,answer_text=NULL,response_time_s=NULL "
                "WHERE id=?", (r["id"],))


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


def repair_all_pairing(conn):
    """Jalankan ulang pairing sadar-thread untuk SEMUA conversation yang ada.
    Berguna sekali jalan setelah upgrade untuk memperbaiki data lama."""
    convs = conn.execute(
        "SELECT DISTINCT platform, conversation_id FROM sosmed_items").fetchall()
    n = 0
    for r in convs:
        _pair_conversation(conn, r[0], r[1])
        n += 1
    conn.commit()
    return {"ok": True, "conversations": n}


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
# Query: Q&A (dulu Inbox / Daftar Q&A)
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
        where.append("item_type='pertanyaan'")
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
# SLA & Analitik: Coverage & SLA
# ---------------------------------------------------------------------------
def _qfilter(platform, range_, start, end, base="item_type='pertanyaan'"):
    where = [base]
    params = []
    if platform:
        where.append("platform=?"); params.append(_norm_platform(platform))
    rng = (range_ or "all").lower()
    s, e = ((start or None), (end or start or None)) if rng == "custom" else resolve_range(rng)
    if s:
        where.append("substr(created_at,1,10)>=?"); params.append(s)
    if e:
        where.append("substr(created_at,1,10)<=?"); params.append(e)
    return " AND ".join(where), params


def coverage_sla(conn, platform="", range_="all", start="", end=""):
    w, params = _qfilter(platform, range_, start, end)
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
# SLA & Analitik: Analytics
# ---------------------------------------------------------------------------
def analytics(conn, platform="", range_="all", start="", end=""):
    # by_day mencakup semua item (volume percakapan), sisanya fokus pertanyaan.
    wall, pall = _qfilter(platform, range_, start, end, base="1=1")
    wq, pq = _qfilter(platform, range_, start, end)
    by_day = [{"day": r[0], "n": r[1]} for r in conn.execute(
        "SELECT substr(created_at,1,10) d, COUNT(*) FROM sosmed_items WHERE " + wall
        + " AND created_at!='' GROUP BY d ORDER BY d", pall).fetchall()]
    by_topik = [{"topik": r[0] or "(tidak terklasifikasi)", "n": r[1]} for r in conn.execute(
        "SELECT topik, COUNT(*) c FROM sosmed_items WHERE " + wq
        + " GROUP BY topik ORDER BY c DESC", pq).fetchall()]
    by_sent = [{"sentiment": r[0] or "netral", "n": r[1]} for r in conn.execute(
        "SELECT sentiment, COUNT(*) c FROM sosmed_items WHERE " + wq
        + " GROUP BY sentiment ORDER BY c DESC", pq).fetchall()]
    by_platform = [{"platform": r[0], "n": r[1]} for r in conn.execute(
        "SELECT platform, COUNT(*) c FROM sosmed_items WHERE " + wall
        + " GROUP BY platform ORDER BY c DESC", pall).fetchall()]
    return {"ok": True, "by_day": by_day, "by_topik": by_topik,
            "by_sentiment": by_sent, "by_platform": by_platform}


# ---------------------------------------------------------------------------
# Pasangan Q&A (pertanyaan root + draf jawaban resmi) — seed FAQ / knowledge
# ---------------------------------------------------------------------------
def faq_pairs(conn, platform="", range_="all", start="", end="",
              only_answered=False, limit=1000):
    """Kembalikan daftar pasangan {pertanyaan, jawaban_draf, ...} dari tiap
    'pertanyaan'. jawaban_draf = balasan resmi (answer_text) bila terjawab.
    Inti bahan FAQ & deteksi gap pengetahuan."""
    w, params = _qfilter(platform, range_, start, end)
    if only_answered:
        w += " AND status='terjawab'"
    rows = conn.execute(
        "SELECT id,platform,conversation_id,external_id,permalink,author_handle,"
        "created_at,text,topik,sentiment,status,answered_by,answered_at,answer_text,"
        "response_time_s,like_count,reply_count,repost_count "
        "FROM sosmed_items WHERE " + w
        + " ORDER BY datetime(created_at) DESC LIMIT ?",
        params + [int(limit)]).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["pertanyaan"] = d.get("text") or ""
        d["jawaban_draf"] = d.get("answer_text") or ""
        out.append(d)
    return {"ok": True, "pairs": out, "total": len(out)}


# ---------------------------------------------------------------------------
# Batches / stats / housekeeping
# ---------------------------------------------------------------------------
def list_batches(conn, limit=100):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM sosmed_batches ORDER BY created_at DESC LIMIT ?",
        (limit,)).fetchall()]


def stats(conn):
    total = conn.execute("SELECT COUNT(*) FROM sosmed_items").fetchone()[0]
    q = conn.execute("SELECT COUNT(*) FROM sosmed_items WHERE item_type='pertanyaan'").fetchone()[0]
    ans = conn.execute("SELECT COUNT(*) FROM sosmed_items WHERE item_type='pertanyaan' "
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
        # Thread c1: pertanyaan root -> balasan resmi (terjawab 25 mnt)
        {"platform": "x", "id": "1001", "conversation_id": "c1",
         "author_handle": "wajibpajak", "created_at": "2026-08-04T10:00:00Z",
         "text": "min saya lupa EFIN gimana cara resetnya? ribet banget"},
        {"platform": "x", "id": "1002", "conversation_id": "c1",
         "in_reply_to_id": "1001", "author_handle": "kring_pajak",
         "created_at": "2026-08-04T10:25:00Z",
         "text": "Halo, silakan hubungi Kring Pajak 1500200 untuk reset EFIN. Terima kasih"},
        # Thread c2: pertanyaan root belum terjawab
        {"platform": "x", "id": "1003", "conversation_id": "c2",
         "author_handle": "orangpajak", "created_at": "2026-08-04T11:00:00Z",
         "text": "cara ganti email yang terdaftar di akun pajak dong min"},
        # Thread c3: pertanyaan root belum terjawab
        {"platform": "x", "id": "1004", "conversation_id": "c3",
         "author_handle": "marahwp", "created_at": "2026-08-04T12:00:00Z",
         "text": "lapor SPT tahunan error terus, kecewa banget lama"},
        # Thread c4 (MULTI-TURN): root -> admin klarifikasi -> customer SUSULAN
        #   -> admin jawab akhir membalas node susulan (beda node dari root).
        {"platform": "x", "id": "2001", "conversation_id": "c4",
         "author_handle": "budi", "created_at": "2026-08-06T09:00:00Z",
         "text": "min mau tanya soal pajak jual tanaman hias kena berapa?"},
        {"platform": "x", "id": "2002", "conversation_id": "c4", "in_reply_to_id": "2001",
         "author_handle": "kring_pajak", "created_at": "2026-08-06T09:10:00Z",
         "text": "Hai Kak, mohon dijelaskan lebih lanjut nominal transaksinya."},
        {"platform": "x", "id": "2003", "conversation_id": "c4", "in_reply_to_id": "2002",
         "author_handle": "budi", "created_at": "2026-08-06T09:20:00Z",
         "text": "nominalnya sekitar 5 juta min"},
        {"platform": "x", "id": "2004", "conversation_id": "c4", "in_reply_to_id": "2003",
         "author_handle": "kring_pajak", "created_at": "2026-08-06T09:30:00Z",
         "text": "Baik Kak, atas transaksi tersebut dikenakan PPh final sesuai ketentuan."},
        # ig1: pertanyaan root belum terjawab (topik Lupa EFIN, utk klaster FAQ)
        {"platform": "ig", "id": "ig1", "conversation_id": "cig",
         "author_handle": "netizen", "created_at": "2026-08-05T09:00:00Z",
         "text": "lupa efin lagi nih, tolong bantu"},
    ]
    r = ingest_items(c, sample, source="import")
    assert r["ok"] and r["n_new"] == 9, r
    assert set(r["platforms"]) == {"x", "ig"}, r

    # klasifikasi topik & sentimen
    it = get_item(c, 1)
    assert it["topik"] == "Lupa EFIN", it["topik"]
    assert it["sentiment"] == "negatif", it["sentiment"]

    # pairing: 1001 pertanyaan terjawab dalam 25 menit + answer_text tersimpan
    assert it["item_type"] == "pertanyaan", it["item_type"]
    assert it["status"] == "terjawab", it["status"]
    assert it["answered_by"] == "kring_pajak", it["answered_by"]
    assert it["response_time_s"] == 25 * 60, it["response_time_s"]
    assert it["answer_text"].startswith("Halo"), it["answer_text"]

    # ganti email -> Perubahan Data, belum terjawab
    it3 = get_item(c, 3)
    assert it3["topik"] == "Perubahan Data", it3["topik"]
    assert it3["item_type"] == "pertanyaan" and it3["status"] == "belum_terjawab", it3

    # MULTI-TURN c4: root 2001 = pertanyaan TERJAWAB (admin jawab node berbeda),
    #   2003 = susulan (bukan pertanyaan), 2002/2004 = balasan_resmi.
    root = get_item(c, 5)
    assert root["external_id"] == "2001", root["external_id"]
    assert root["item_type"] == "pertanyaan" and root["status"] == "terjawab", root
    assert root["response_time_s"] == 10 * 60, root["response_time_s"]  # jawaban resmi paling awal
    sus = get_item(c, 7)
    assert sus["external_id"] == "2003" and sus["item_type"] == "susulan", sus
    off2 = get_item(c, 6)
    assert off2["item_type"] == "balasan_resmi", off2

    # daftar pertanyaan: 1001,1003,1004,2001,ig1 = 5 (susulan TIDAK termasuk)
    lst = list_items(c, only_questions=True)
    assert lst["total"] == 5, lst["total"]
    lst_x = list_items(c, platform="x", topik="Lupa EFIN", only_questions=True)
    assert lst_x["total"] == 1, lst_x["total"]

    # thread multi-turn utuh
    th = get_thread(c, "x", "c4")
    assert th and th["n"] == 4, th

    # coverage & SLA: 5 pertanyaan, 2 terjawab (1001, 2001)
    cov = coverage_sla(c)
    assert cov["total"] == 5 and cov["answered"] == 2, cov
    assert cov["within_sla"] == 2, cov  # 25 & 10 mnt <= 60 mnt

    # analytics
    an = analytics(c)
    topik_names = {x["topik"] for x in an["by_topik"]}
    assert "Lupa EFIN" in topik_names, an["by_topik"]

    # faq_pairs: pertanyaan terjawab membawa draf jawaban
    fp = faq_pairs(c, only_answered=True)
    assert fp["total"] == 2, fp["total"]
    assert all(p["jawaban_draf"] for p in fp["pairs"]), fp["pairs"]

    # idempotensi: ingest ulang tidak menambah baris & item_type stabil
    r2 = ingest_items(c, sample, source="import")
    assert r2["n_new"] == 0 and r2["n_dup"] == 9, r2
    assert get_item(c, 7)["item_type"] == "susulan", "item_type harus stabil"

    # repair_all_pairing aman dijalankan ulang
    rep = repair_all_pairing(c)
    assert rep["ok"], rep

    st = stats(c)
    assert st["total"] == 9 and st["questions"] == 5 and st["answered"] == 2, st

    c.close()
    print("SOSMED_DB_SMOKE_OK")
