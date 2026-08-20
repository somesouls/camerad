# -*- coding: utf-8 -*-
"""
analytics_db.py
----------------
Lapisan penyimpanan (SQLite) untuk sistem analitik chatbot holistik.

Menyimpan setiap interaksi Dialogflow yang ditarik dari Google Cloud Logging
secara periodik, lalu menyediakan query siap-pakai untuk dashboard & AI
tanya-jawab (text-to-SQL yang aman / read-only).

Hanya memakai stdlib (sqlite3). Tidak butuh server database.
"""
import os
import re
import sqlite3
import datetime as _dt

# --- Klasifikasi intent (identik dengan web_app.step2) -------------------
SYSTEM_INTENTS = {"System_System_Welcome Intent", "System_System_Hubungi Agent"}
FALLBACK_INTENTS = {"System_System_Fallback Intent", "System_System_Fallback Intent 2"}

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # paket db/ -> root repo


def default_db_path():
    return os.environ.get("PIPELINE_DB_FILE") or os.path.join(_BASE_DIR, "analytics.db")


def _jkt_today():
    try:
        from zoneinfo import ZoneInfo
        return _dt.datetime.now(ZoneInfo("Asia/Jakarta")).strftime("%Y-%m-%d")
    except Exception:
        tz = _dt.timezone(_dt.timedelta(hours=7))
        return _dt.datetime.now(tz).strftime("%Y-%m-%d")


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
        CREATE TABLE IF NOT EXISTS interactions (
            insert_id     TEXT PRIMARY KEY,
            session_id    TEXT,
            ts            TEXT,          -- ISO timestamp interaksi (apa adanya dari log)
            day           TEXT,          -- YYYY-MM-DD (Asia/Jakarta) untuk filter cepat
            user_phrase   TEXT,
            bot_response  TEXT,
            intent_name   TEXT,
            lang          TEXT,
            score         REAL,
            is_fallback   INTEGER DEFAULT 0,
            is_system     INTEGER DEFAULT 0,
            is_one_char   INTEGER DEFAULT 0,
            created_at    TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_int_day       ON interactions(day);
        CREATE INDEX IF NOT EXISTS idx_int_intent    ON interactions(intent_name);
        CREATE INDEX IF NOT EXISTS idx_int_fallback  ON interactions(is_fallback);
        CREATE INDEX IF NOT EXISTS idx_int_session   ON interactions(session_id);

        CREATE TABLE IF NOT EXISTS ingest_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at  TEXT,
            finished_at TEXT,
            start_date  TEXT,
            end_date    TEXT,
            lang        TEXT,
            fetched     INTEGER DEFAULT 0,
            inserted    INTEGER DEFAULT 0,
            skipped     INTEGER DEFAULT 0,
            status      TEXT,
            note        TEXT
        );

        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS day_status (
            day              TEXT,
            lang             TEXT,
            status           TEXT,        -- 'complete' | 'partial'
            fetched          INTEGER DEFAULT 0,
            inserted         INTEGER DEFAULT 0,
            first_fetched_at TEXT,
            last_fetched_at  TEXT,
            PRIMARY KEY (day, lang)
        );

        CREATE TABLE IF NOT EXISTS raw_entries (
            insert_id  TEXT PRIMARY KEY,
            day        TEXT,
            lang       TEXT,
            ts         TEXT,
            raw_json   TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_raw_daylang ON raw_entries(lang, day);

        CREATE TABLE IF NOT EXISTS candidate_status (
            phrase_norm TEXT PRIMARY KEY,
            phrase      TEXT,
            status      TEXT,
            note        TEXT,
            updated_at  TEXT,
            updated_by  TEXT,
            followup_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_candstat_status ON candidate_status(status);

        CREATE TABLE IF NOT EXISTS intent_status (
            intent_name TEXT PRIMARY KEY,
            status      TEXT,
            note        TEXT,
            updated_at  TEXT,
            updated_by  TEXT,
            followup_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_intentstat_status ON intent_status(status);
        """
    )
    conn.commit()
    return conn


def set_meta(conn, key, value):
    conn.execute(
        "INSERT INTO meta(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    conn.commit()


def get_meta(conn, key, default=None):
    r = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def classify(intent_name, user_phrase):
    it = intent_name or ""
    phrase = (user_phrase or "").strip()
    is_system = 1 if it in SYSTEM_INTENTS else 0
    is_fallback = 1 if it in FALLBACK_INTENTS else 0
    is_one_char = 1 if (len(phrase) == 1) else 0
    return is_system, is_fallback, is_one_char


def _day_from_ts(ts):
    ts = (ts or "").strip()
    m = re.match(r"(\d{4}-\d{2}-\d{2})", ts)
    if m:
        return m.group(1)
    return _jkt_today()


def upsert_interactions(conn, rows):
    """rows: list of dict dengan kunci gaya step2:
    'ID trace','waktu interaksi','user phrase','bot response','intent name',
    'lang','insertId','score'. Dedup berdasarkan insertId. Baris tanpa insertId
    dilewati (tak bisa dedup dengan aman).
    Return (inserted, skipped)."""
    now = _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    inserted = 0
    skipped = 0
    cur = conn.cursor()
    for r in rows:
        insert_id = str(r.get("insertId") or "").strip()
        if not insert_id:
            skipped += 1
            continue
        intent_name = r.get("intent name") or ""
        user_phrase = r.get("user phrase") or ""
        ts = r.get("waktu interaksi") or ""
        is_system, is_fallback, is_one_char = classify(intent_name, user_phrase)
        score = r.get("score")
        try:
            score = float(score) if score not in ("", None) else None
        except Exception:
            score = None
        try:
            cur.execute(
                "INSERT OR IGNORE INTO interactions("
                "insert_id,session_id,ts,day,user_phrase,bot_response,intent_name,"
                "lang,score,is_fallback,is_system,is_one_char,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    insert_id, r.get("ID trace") or "", ts, _day_from_ts(ts),
                    user_phrase, r.get("bot response") or "", intent_name,
                    r.get("lang") or "", score, is_fallback, is_system, is_one_char, now,
                ),
            )
            if cur.rowcount > 0:
                inserted += 1
            else:
                skipped += 1
        except Exception:
            skipped += 1
    conn.commit()
    return inserted, skipped


def log_ingest(conn, **kw):
    conn.execute(
        "INSERT INTO ingest_log(started_at,finished_at,start_date,end_date,lang,"
        "fetched,inserted,skipped,status,note) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            kw.get("started_at"), kw.get("finished_at"), kw.get("start_date"),
            kw.get("end_date"), kw.get("lang"), kw.get("fetched", 0),
            kw.get("inserted", 0), kw.get("skipped", 0), kw.get("status", ""),
            kw.get("note", ""),
        ),
    )
    conn.commit()


# =====================================================================
# Rentang tanggal
# =====================================================================
def resolve_range(preset=None, start=None, end=None):
    """Kembalikan (start_day, end_day) inklusif, atau (None,None) untuk 'all'.
    preset: 'today','yesterday','7d','30d','90d','all','custom'."""
    today = _jkt_today()
    td = _dt.datetime.strptime(today, "%Y-%m-%d").date()
    p = (preset or "").lower()
    if p == "all":
        return None, None
    if p == "today":
        return today, today
    if p == "yesterday":
        y = (td - _dt.timedelta(days=1)).strftime("%Y-%m-%d")
        return y, y
    if p in ("7d", "7"):
        return (td - _dt.timedelta(days=6)).strftime("%Y-%m-%d"), today
    if p in ("30d", "30"):
        return (td - _dt.timedelta(days=29)).strftime("%Y-%m-%d"), today
    if p in ("90d", "90"):
        return (td - _dt.timedelta(days=89)).strftime("%Y-%m-%d"), today
    # custom / fallback
    return (start or None), (end or None)


def _range_where(start, end, col="day", params=None):
    params = params if params is not None else []
    clauses = []
    if start:
        clauses.append(col + " >= ?")
        params.append(start)
    if end:
        clauses.append(col + " <= ?")
        params.append(end)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


# =====================================================================
# Query analitik untuk dashboard
# =====================================================================
def _lang_where(where, params, lang):
    if lang:
        where += (" AND " if where else " WHERE ") + "lang=?"
        params.append(str(lang).lower())
    return where


def _class_expr(include_system=False, include_umum=False):
    """Ekspresi filter kelas intent berbasis AWALAN nama (query-time).
    Default mengecualikan intent 'System_' (termasuk fallback yang bernama
    'System_System_Fallback Intent' & '...Fallback Intent 2') dan 'Umum_',
    sehingga dashboard menampilkan 'intent bersih'. Memakai substr() agar
    aman dari wildcard SQL (tanpa perlu ESCAPE) dan tanpa parameter."""
    parts = []
    if not include_system:
        parts.append("substr(intent_name,1,7) <> 'System_'")
    if not include_umum:
        parts.append("substr(intent_name,1,5) <> 'Umum_'")
    return parts


def _apply_class(where, include_system=False, include_umum=False):
    parts = _class_expr(include_system, include_umum)
    if parts:
        where += (" AND " if where else " WHERE ") + " AND ".join(parts)
    return where


def overview(conn, start=None, end=None, lang=None, include_system=False, include_umum=False):
    where, params = _range_where(start, end)
    where = _lang_where(where, params, lang)
    clean = _class_expr(include_system, include_umum)
    clean_sql = (" AND ".join(clean)) if clean else "1=1"
    row = conn.execute(
        "SELECT COUNT(*) AS total, "
        "COUNT(DISTINCT session_id) AS sessions, "
        "SUM(is_fallback) AS fallback, "
        "SUM(CASE WHEN intent_name='System_System_Fallback Intent' THEN 1 ELSE 0 END) AS fallback1, "
        "SUM(CASE WHEN intent_name='System_System_Fallback Intent 2' THEN 1 ELSE 0 END) AS fallback2, "
        "SUM(is_system) AS system, "
        "COUNT(DISTINCT CASE WHEN " + clean_sql + " THEN intent_name END) AS intents "
        "FROM interactions" + where,
        params,
    ).fetchone()
    total = row["total"] or 0
    fb = row["fallback"] or 0
    return {
        "total": total,
        "sessions": row["sessions"] or 0,
        "fallback": fb,
        "fallback_rate": round((fb / total * 100.0), 2) if total else 0.0,
        "fallback1": row["fallback1"] or 0,
        "fallback2": row["fallback2"] or 0,
        "fallback1_rate": round(((row["fallback1"] or 0) / total * 100.0), 2) if total else 0.0,
        "fallback2_rate": round(((row["fallback2"] or 0) / total * 100.0), 2) if total else 0.0,
        "system": row["system"] or 0,
        "distinct_intents": row["intents"] or 0,
    }


def top_intents(conn, start=None, end=None, limit=15, include_system=False, include_umum=False, lang=None):
    where, params = _range_where(start, end)
    where = _lang_where(where, params, lang)
    where = _apply_class(where, include_system, include_umum)
    params.append(int(limit))
    rows = conn.execute(
        "SELECT intent_name, COUNT(*) AS count FROM interactions" + where +
        " GROUP BY intent_name ORDER BY count DESC, intent_name ASC LIMIT ?",
        params,
    ).fetchall()
    return [{"intent": r["intent_name"] or "(kosong)", "count": r["count"]} for r in rows]


def volume_by_day(conn, start=None, end=None, lang=None):
    where, params = _range_where(start, end)
    where = _lang_where(where, params, lang)
    rows = conn.execute(
        "SELECT day, COUNT(*) AS total, SUM(is_fallback) AS fallback, "
        "SUM(CASE WHEN intent_name='System_System_Fallback Intent' THEN 1 ELSE 0 END) AS fallback1, "
        "SUM(CASE WHEN intent_name='System_System_Fallback Intent 2' THEN 1 ELSE 0 END) AS fallback2 "
        "FROM interactions" + where + " GROUP BY day ORDER BY day ASC",
        params,
    ).fetchall()
    return [{"day": r["day"], "total": r["total"], "fallback": r["fallback"] or 0, "fallback1": r["fallback1"] or 0, "fallback2": r["fallback2"] or 0} for r in rows]


def _norm_phrase(p):
    p = (p or "").strip().lower()
    p = re.sub(r"\s+", " ", p)
    return p


def new_questions(conn, start=None, end=None, limit=25, min_len=2, lang=None):
    """Pertanyaan yang jatuh ke fallback (belum ada intent-nya), dikelompokkan
    per frasa (dinormalisasi) dan diurut dari yang paling sering."""
    where, params = _range_where(start, end)
    where = _lang_where(where, params, lang)
    extra = (" AND " if where else " WHERE ") + "is_fallback=1"
    rows = conn.execute(
        "SELECT user_phrase FROM interactions" + where + extra,
        params,
    ).fetchall()
    counts = {}
    for r in rows:
        raw = (r["user_phrase"] or "").strip()
        norm = _norm_phrase(raw)
        if len(norm) < min_len:
            continue
        if norm not in counts:
            counts[norm] = {"phrase": raw, "count": 0}
        counts[norm]["count"] += 1
    out = sorted(counts.values(), key=lambda x: (-x["count"], x["phrase"]))
    return out[: int(limit)]


def hot_topics(conn, start=None, end=None, limit=15, stopwords=None, lang=None):
    """Perkiraan 'topik terhangat' berbasis kata kunci dari user phrase
    non-system (v1, tanpa model). Bisa di-upgrade ke clustering semantik."""
    where, params = _range_where(start, end)
    where = _lang_where(where, params, lang)
    extra = (" AND " if where else " WHERE ") + "is_system=0"
    rows = conn.execute(
        "SELECT user_phrase FROM interactions" + where + extra,
        params,
    ).fetchall()
    sw = stopwords or _DEFAULT_STOPWORDS
    freq = {}
    for r in rows:
        for w in re.findall(r"[a-zA-Z\u00c0-\u024f]{3,}", (r["user_phrase"] or "").lower()):
            if w in sw:
                continue
            freq[w] = freq.get(w, 0) + 1
    out = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))[: int(limit)]
    return [{"keyword": k, "count": c} for k, c in out]


def search_intents(conn, term, limit=25):
    """Cari intent berdasarkan kecocokan teks pada nama intent ATAU pada frasa
    user yang memicunya (fallback keyword-based untuk 'intent terkait X')."""
    like = "%" + (term or "").strip() + "%"
    rows = conn.execute(
        "SELECT intent_name, COUNT(*) AS count, "
        "MIN(user_phrase) AS sample "
        "FROM interactions "
        "WHERE is_system=0 AND is_fallback=0 "
        "AND (intent_name LIKE ? OR user_phrase LIKE ?) "
        "GROUP BY intent_name ORDER BY count DESC LIMIT ?",
        (like, like, int(limit)),
    ).fetchall()
    return [
        {"intent": r["intent_name"], "count": r["count"], "sample": r["sample"]}
        for r in rows
    ]


def data_bounds(conn):
    r = conn.execute(
        "SELECT MIN(day) AS min_day, MAX(day) AS max_day, COUNT(*) AS total FROM interactions"
    ).fetchone()
    return {"min_day": r["min_day"], "max_day": r["max_day"], "total": r["total"] or 0}


# =====================================================================
# Text-to-SQL yang aman (read-only) untuk AI tanya-jawab data
# =====================================================================
SCHEMA_FOR_LLM = (
    "Tabel SQLite `interactions` (satu baris = satu interaksi user-bot):\n"
    "- insert_id TEXT (PK)\n"
    "- session_id TEXT (id percakapan)\n"
    "- ts TEXT (timestamp ISO)\n"
    "- day TEXT (YYYY-MM-DD, zona Asia/Jakarta) <- pakai ini untuk filter tanggal\n"
    "- user_phrase TEXT (pertanyaan user)\n"
    "- bot_response TEXT (jawaban bot)\n"
    "- intent_name TEXT (nama intent yang match)\n"
    "- lang TEXT ('id'/'en')\n"
    "- score REAL (confidence 0..1)\n"
    "- is_fallback INTEGER (1 = pertanyaan tak dikenali/fallback)\n"
    "- is_system INTEGER (1 = intent sistem spt welcome/hubungi agent)\n"
    "- is_one_char INTEGER (1 = input 1 karakter)\n"
    "Catatan: 'intent terbanyak' biasanya filter is_system=0 AND is_fallback=0. "
    "'pertanyaan baru tanpa intent' = is_fallback=1."
)

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|"
    r"pragma|vacuum|reindex|analyze|truncate)\b",
    re.I,
)


def run_select(conn, sql, max_rows=200):
    """Jalankan SATU query SELECT/WITH read-only. Menolak apapun yang bisa
    mengubah data. Return {ok, columns, rows} atau {ok:False, error}."""
    q = (sql or "").strip().rstrip(";").strip()
    if not q:
        return {"ok": False, "error": "Query kosong."}
    low = q.lower()
    if not (low.startswith("select") or low.startswith("with")):
        return {"ok": False, "error": "Hanya query SELECT yang diizinkan."}
    if ";" in q:
        return {"ok": False, "error": "Hanya satu statement yang diizinkan."}
    if _FORBIDDEN.search(q):
        return {"ok": False, "error": "Query mengandung operasi yang tidak diizinkan."}
    # paksa LIMIT bila belum ada
    if not re.search(r"\blimit\b", low):
        q = q + (" LIMIT %d" % int(max_rows))
    try:
        cur = conn.execute(q)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = [list(r) for r in cur.fetchmany(int(max_rows))]
        return {"ok": True, "columns": cols, "rows": rows, "sql": q}
    except Exception as e:
        return {"ok": False, "error": str(e), "sql": q}


_DEFAULT_STOPWORDS = set(
    "yang dan atau apa apakah bagaimana kenapa mengapa saya aku kamu anda kita "
    "itu ini ada tidak nggak gak bisa mau ingin untuk dari ke di pada dengan "
    "adalah akan sudah belum juga saja lagi kok sih dong ya gimana kalau jika "
    "the and for you are how what why with this that not can want your our "
    "tolong mohon min mas mbak pak bu halo hai hallo assalamualaikum terima kasih".split()
)


# =====================================================================
# Kelengkapan data per hari + penyimpanan log mentah (untuk Step 1)
# =====================================================================
def list_days(start, end):
    """Daftar 'YYYY-MM-DD' inklusif dari start..end."""
    if not start or not end:
        return []
    d0 = _dt.datetime.strptime(start, "%Y-%m-%d").date()
    d1 = _dt.datetime.strptime(end, "%Y-%m-%d").date()
    if d1 < d0:
        d0, d1 = d1, d0
    out = []
    d = d0
    while d <= d1:
        out.append(d.strftime("%Y-%m-%d"))
        d += _dt.timedelta(days=1)
    return out


def set_day_status(conn, day, lang, status, fetched=0, inserted=0):
    """Tandai status kelengkapan sebuah hari (per bahasa).
    status: 'complete' (hari sudah lewat penuh) atau 'partial' (masih berjalan)."""
    now = _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "INSERT INTO day_status(day,lang,status,fetched,inserted,first_fetched_at,last_fetched_at) "
        "VALUES(?,?,?,?,?,?,?) "
        "ON CONFLICT(day,lang) DO UPDATE SET status=excluded.status, "
        "fetched=excluded.fetched, inserted=day_status.inserted+excluded.inserted, "
        "last_fetched_at=excluded.last_fetched_at",
        (day, lang, status, int(fetched), int(inserted), now, now),
    )
    conn.commit()


def get_day_status(conn, day, lang):
    r = conn.execute(
        "SELECT day,lang,status,fetched,inserted,first_fetched_at,last_fetched_at "
        "FROM day_status WHERE day=? AND lang=?", (day, lang)).fetchone()
    return dict(r) if r else None


def range_status(conn, start, end, lang):
    """Status kelengkapan tiap hari pada rentang, untuk halaman Kelola Data &
    indikator di dashboard. status per hari: 'complete'|'partial'|'missing'."""
    days = list_days(start, end)
    out = []
    complete = partial = missing = 0
    for d in days:
        st = get_day_status(conn, d, lang)
        cnt = conn.execute(
            "SELECT COUNT(*) FROM interactions WHERE day=? AND lang=?",
            (d, lang)).fetchone()[0]
        status = st["status"] if st else "missing"
        if status == "complete":
            complete += 1
        elif status == "partial":
            partial += 1
        else:
            status = "missing"
            missing += 1
        out.append({"day": d, "status": status, "rows": cnt,
                    "fetched": (st or {}).get("fetched", 0),
                    "last_fetched_at": (st or {}).get("last_fetched_at")})
    return {"lang": lang, "start": start, "end": end, "days": out,
            "complete": complete, "partial": partial, "missing": missing,
            "total_days": len(days)}


def days_to_fetch(conn, start, end, lang, force=False):
    """Hari yang perlu ditarik: 'missing' atau 'partial' (atau semua bila force).
    Hari 'complete' dilewati."""
    todo = []
    for d in list_days(start, end):
        if force:
            todo.append(d)
            continue
        st = get_day_status(conn, d, lang)
        if not st or st["status"] != "complete":
            todo.append(d)
    return todo


def range_complete(conn, start, end, lang):
    """True bila semua hari pada rentang sudah 'complete' di DB."""
    days = list_days(start, end)
    if not days:
        return False
    for d in days:
        st = get_day_status(conn, d, lang)
        if not st or st["status"] != "complete":
            return False
    return True


def upsert_raw_entries(conn, entries, day, lang):
    """Simpan entri log MENTAH (untuk membangun ulang output Step 1 dari DB).
    Dedup via insertId. Return jumlah entri baru."""
    import json as _json
    inserted = 0
    cur = conn.cursor()
    for e in entries:
        if not isinstance(e, dict):
            continue
        iid = str(e.get("insertId") or "").strip()
        if not iid:
            continue
        ts = e.get("timestamp") or ""
        try:
            cur.execute(
                "INSERT OR IGNORE INTO raw_entries(insert_id,day,lang,ts,raw_json) "
                "VALUES(?,?,?,?,?)",
                (iid, day, lang, ts, _json.dumps(e, ensure_ascii=False)))
            if cur.rowcount > 0:
                inserted += 1
        except Exception:
            pass
    conn.commit()
    return inserted


def rebuild_entries_json(conn, start, end, lang):
    """Bangun ulang array JSON entri log mentah (format identik output Step 1)
    dari data yang tersimpan di DB. Return (content_str, jumlah_entri)."""
    rows = conn.execute(
        "SELECT raw_json FROM raw_entries WHERE lang=? AND day>=? AND day<=? "
        "ORDER BY ts ASC, insert_id ASC", (lang, start, end)).fetchall()
    chunks = [r["raw_json"] for r in rows]
    content = ("[\n" + ",\n".join(chunks) + "\n]\n") if chunks else "[]\n"
    return content, len(chunks)


if __name__ == "__main__":
    # smoke test cepat tanpa jaringan
    import tempfile
    p = os.path.join(tempfile.gettempdir(), "analytics_smoke.db")
    if os.path.exists(p):
        os.remove(p)
    c = init_db(connect(p))
    sample = [
        {"insertId": "a1", "ID trace": "s1", "waktu interaksi": "2026-07-20T09:00:00Z",
         "user phrase": "cara lapor SPT tahunan", "bot response": "...",
         "intent name": "Pajak_SPT_Tahunan", "lang": "id", "score": 0.9},
        {"insertId": "a2", "ID trace": "s1", "waktu interaksi": "2026-07-20T09:01:00Z",
         "user phrase": "lapor pajak online gimana", "bot response": "...",
         "intent name": "System_System_Fallback Intent", "lang": "id", "score": 0.2},
        {"insertId": "a3", "ID trace": "s2", "waktu interaksi": "2026-07-21T10:00:00Z",
         "user phrase": "lapor pajak online gimana", "bot response": "...",
         "intent name": "System_System_Fallback Intent", "lang": "id", "score": 0.2},
        {"insertId": "a2", "ID trace": "s1", "waktu interaksi": "2026-07-20T09:01:00Z",
         "user phrase": "dup", "bot response": "...", "intent name": "X", "lang": "id", "score": 0.2},
    ]
    ins, skip = upsert_interactions(c, sample)
    assert ins == 3 and skip == 1, (ins, skip)
    ov = overview(c)
    assert ov["total"] == 3 and ov["fallback"] == 2, ov
    nq = new_questions(c)
    assert nq and nq[0]["count"] == 2, nq
    si = search_intents(c, "SPT")
    assert si and si[0]["intent"] == "Pajak_SPT_Tahunan", si
    rs = run_select(c, "SELECT COUNT(*) AS n FROM interactions")
    assert rs["ok"] and rs["rows"][0][0] == 3, rs
    bad = run_select(c, "DELETE FROM interactions")
    assert not bad["ok"], bad
    print("analytics_db smoke test: OK", ov, nq[0], si[0])


# =====================================================================
# FASE 2 — Analitik Deflection, Sesi & Drill-down Kandidat (Epik D1-D3, D6)
# ---------------------------------------------------------------------
# Catatan zona waktu: kolom `ts` berformat ISO UTC (akhiran 'Z').
# Untuk analisis jam sibuk / jam kerja kita konversi ke WIB (+7 jam).
# Konfirmasi empiris: distribusi jam +7 memuncak 09-14 WIB (jam kerja),
# sedangkan UTC mentah memuncak dini hari (tidak masuk akal untuk KPP).
# =====================================================================

# Nama intent sinyal (persis seperti di data Dialogflow)
AGENT_1500200 = "System_System_Hubungi Agent"            # dipicu ketik 1500200
AGENT_CONNECTOR = "System_System_Hubungi Agent Connector"  # dipicu kata kunci/typo
FALLBACK_1 = "System_System_Fallback Intent"
FALLBACK_2 = "System_System_Fallback Intent 2"

# ts (UTC, buang fraksi detik & 'Z', ganti 'T' -> spasi) lalu +7 jam = WIB
_WIB = "datetime(replace(substr(ts,1,19),'T',' '), '+7 hours')"

# Label ramah untuk tiap kategori perjalanan sesi (D1)
JOURNEY_LABELS = {
    "self_served": "Dilayani mandiri (tanpa fallback/agent)",
    "fallback_abandon": "Fallback lalu ditinggalkan",
    "fallback2_no_agent": "Fallback ganda (eskalasi) tanpa tersambung agent",
    "agent_1500200": "Tersambung agent via ketik 1500200",
    "agent_connector": "Tersambung agent via kata kunci/typo",
}


def _session_flags_sql(where):
    """SQL agregasi per sesi: bendera fallback1/2, agent(1500200), connector,
    jumlah interaksi, dan jumlah 'clean hit' (intent bersih non-fallback)."""
    return (
        "SELECT session_id, "
        "COUNT(*) AS n, "
        "MIN(ts) AS ts_first, MAX(ts) AS ts_last, "
        "MAX(CASE WHEN intent_name=? THEN 1 ELSE 0 END) AS fb1, "
        "MAX(CASE WHEN intent_name=? THEN 1 ELSE 0 END) AS fb2, "
        "MAX(CASE WHEN intent_name=? THEN 1 ELSE 0 END) AS agent, "
        "MAX(CASE WHEN intent_name=? THEN 1 ELSE 0 END) AS connector, "
        "SUM(CASE WHEN is_fallback=1 THEN 1 ELSE 0 END) AS fb_count, "
        "SUM(CASE WHEN is_fallback=0 AND substr(intent_name,1,7)<>'System_' "
        "AND substr(intent_name,1,5)<>'Umum_' THEN 1 ELSE 0 END) AS clean_hits "
        "FROM interactions" + where + " GROUP BY session_id"
    )


def _classify_session(r):
    """Klasifikasikan satu sesi ke satu kategori perjalanan (prioritas)."""
    if r["agent"]:
        return "agent_1500200"       # tersambung agent (ketik 1500200)
    if r["connector"]:
        return "agent_connector"     # tersambung agent (kata kunci/typo)
    if r["fb2"]:
        return "fallback2_no_agent"  # eskalasi fallback-2 tapi tak tersambung
    if r["fb1"]:
        return "fallback_abandon"    # fallback lalu ditinggalkan
    return "self_served"             # dilayani mandiri


def _session_rows(conn, start=None, end=None, lang=None):
    where, params = _range_where(start, end)
    where = _lang_where(where, params, lang)
    head = [FALLBACK_1, FALLBACK_2, AGENT_1500200, AGENT_CONNECTOR]
    return conn.execute(_session_flags_sql(where), head + params).fetchall()


def session_journeys(conn, start=None, end=None, lang=None):
    """D1 — Rekonstruksi & klasifikasi perjalanan fallback per sesi."""
    rows = _session_rows(conn, start, end, lang)
    buckets = {k: 0 for k in JOURNEY_LABELS}
    fb_before_agent = 0
    fb1_total = fb2_total = 0
    total = 0
    for r in rows:
        total += 1
        buckets[_classify_session(r)] += 1
        if (r["agent"] or r["connector"]) and (r["fb1"] or r["fb2"]):
            fb_before_agent += 1
        if r["fb1"]:
            fb1_total += 1
        if r["fb2"]:
            fb2_total += 1
    items = []
    for k, lbl in JOURNEY_LABELS.items():
        c = buckets[k]
        items.append({
            "key": k, "label": lbl, "count": c,
            "pct": round(c / total * 100.0, 1) if total else 0.0,
        })
    items.sort(key=lambda x: -x["count"])
    return {
        "total_sessions": total,
        "items": items,
        "fallback_before_agent": fb_before_agent,
        "sessions_with_fallback1": fb1_total,
        "sessions_with_fallback2": fb2_total,
    }


def hourly_load(conn, start=None, end=None, lang=None,
                work_days=(1, 2, 3, 4, 5), work_start=8, work_end=16):
    """D2 — Beban per jam (WIB) + matriks hari x jam untuk heatmap.
    work_days: 0=Minggu..6=Sabtu (default Sen-Jum). Jam kerja [work_start, work_end)."""
    where, params = _range_where(start, end)
    where = _lang_where(where, params, lang)
    rows = conn.execute(
        "SELECT CAST(strftime('%w', " + _WIB + ") AS INTEGER) AS dow, "
        "CAST(strftime('%H', " + _WIB + ") AS INTEGER) AS hh, "
        "COUNT(*) AS n FROM interactions" + where + " GROUP BY dow, hh",
        params,
    ).fetchall()
    matrix = [[0] * 24 for _ in range(7)]
    by_hour = [0] * 24
    for r in rows:
        d, h = r["dow"], r["hh"]
        if d is None or h is None:
            continue
        matrix[d][h] += r["n"]
        by_hour[h] += r["n"]
    wd = set(work_days)
    work = off = 0
    for d in range(7):
        for h in range(24):
            v = matrix[d][h]
            if d in wd and work_start <= h < work_end:
                work += v
            else:
                off += v
    total = work + off
    peak_hour = max(range(24), key=lambda h: by_hour[h]) if total else None
    return {
        "matrix": matrix, "by_hour": by_hour,
        "work": work, "off": off, "total": total,
        "work_pct": round(work / total * 100.0, 1) if total else 0.0,
        "peak_hour": peak_hour,
        "work_days": sorted(wd), "work_start": work_start, "work_end": work_end,
    }


def service_quality(conn, start=None, end=None, lang=None):
    """D3 — Kualitas self-service & keandalan.
    Keandalan dihitung HANYA pada sesi yang benar-benar memakai bot,
    yaitu MENGECUALIKAN sesi yang menghubungi agent (baik via 1500200
    maupun via kata kunci/typo lewat Connector)."""
    j = session_journeys(conn, start, end, lang)
    total = j["total_sessions"] or 0
    b = {it["key"]: it["count"] for it in j["items"]}
    to_1500200 = b.get("agent_1500200", 0)
    to_connector = b.get("agent_connector", 0)
    to_agent = to_1500200 + to_connector
    bot_only = total - to_agent                 # sesi yang benar-benar pakai bot
    bot_resolved = b.get("self_served", 0)       # tuntas tanpa fallback
    bot_fallback = bot_only - bot_resolved       # fallback tapi tak ke agent
    return {
        "total_sessions": total,
        "to_agent_1500200": to_1500200,
        "to_agent_connector": to_connector,
        "to_agent_total": to_agent,
        "agent_rate": round(to_agent / total * 100.0, 2) if total else 0.0,
        "bot_only_sessions": bot_only,
        "bot_only_resolved": bot_resolved,
        "bot_only_fallback": bot_fallback,
        "self_service_rate": round(bot_resolved / total * 100.0, 2) if total else 0.0,
        "reliability_rate": round(bot_resolved / bot_only * 100.0, 2) if bot_only else 0.0,
    }


def deflection_overview(conn, start=None, end=None, lang=None,
                        work_days=(1, 2, 3, 4, 5), work_start=8, work_end=16):
    """Gabungan D1+D2+D3 untuk satu panggilan ringkas ke halaman deflection."""
    return {
        "journeys": session_journeys(conn, start, end, lang),
        "hourly": hourly_load(conn, start, end, lang, work_days, work_start, work_end),
        "quality": service_quality(conn, start, end, lang),
    }


# --------------------------- D6: Kandidat drill-down ---------------------------
def candidate_list(conn, start=None, end=None, lang=None, limit=200, min_len=2):
    """Daftar Kandidat Intent Baru (frasa fallback) + status tindak lanjut."""
    items = new_questions(conn, start, end, limit=limit, min_len=min_len, lang=lang)
    st = get_candidate_statuses(conn, [it["phrase"] for it in items])
    for it in items:
        s = st.get(_norm_phrase(it["phrase"]))
        it["status"] = (s or {}).get("status", "")
        it["note"] = (s or {}).get("note", "")
        it["followup_at"] = (s or {}).get("followup_at")
    return items


def candidate_detail(conn, phrase, start=None, end=None, lang=None, max_sessions=50):
    """Drill-down satu frasa fallback: berapa kali muncul, di sesi mana,
    dan intent BERSIH apa saja yang muncul bersamaan (pecahan topik)."""
    target = _norm_phrase(phrase)
    where, params = _range_where(start, end)
    where = _lang_where(where, params, lang)
    extra = (" AND " if where else " WHERE ") + "is_fallback=1"
    rows = conn.execute(
        "SELECT session_id, ts, day, user_phrase FROM interactions" + where + extra,
        params,
    ).fetchall()
    sess = {}
    occ = 0
    for r in rows:
        if _norm_phrase(r["user_phrase"]) != target:
            continue
        occ += 1
        sess.setdefault(r["session_id"], []).append(r["ts"])
    session_ids = list(sess.keys())
    # Pecahan topik = intent BERSIH yang terpanggil TEPAT sebelum turn fallback
    # (per kejadian), bukan sekadar intent yang co-occur di sesi yang sama.
    co = {}
    sess_intents = {}
    if session_ids:
        qmarks = ",".join("?" * len(session_ids))
        crows = conn.execute(
            "SELECT session_id, ts, intent_name, is_fallback, user_phrase, insert_id "
            "FROM interactions WHERE session_id IN (" + qmarks + ") "
            "ORDER BY session_id, ts, insert_id",
            session_ids,
        ).fetchall()
        by_sess = {}
        for cr in crows:
            by_sess.setdefault(cr["session_id"], []).append(cr)
        for sid, seq in by_sess.items():
            for i, row in enumerate(seq):
                if not row["is_fallback"]:
                    continue
                if _norm_phrase(row["user_phrase"]) != target:
                    continue
                # telusuri mundur: intent bersih terdekat sebelum turn fallback ini
                prev = ""
                j = i - 1
                while j >= 0:
                    pn = seq[j]["intent_name"] or ""
                    if (not seq[j]["is_fallback"]) and pn \
                            and not pn.startswith("System_") \
                            and not pn.startswith("Umum_"):
                        prev = pn
                        break
                    j -= 1
                if prev:
                    co[prev] = co.get(prev, 0) + 1
                    sess_intents.setdefault(sid, set()).add(prev)
    co_list = sorted(
        [{"intent": k, "count": v} for k, v in co.items()],
        key=lambda x: (-x["count"], x["intent"]),
    )[:20]
    # status tindak lanjut per INTENT (pecahan topik), bukan per frasa
    _ist = get_intent_statuses(conn, [c["intent"] for c in co_list])
    for c in co_list:
        _s = _ist.get(c["intent"]) or {}
        c["status"] = _s.get("status", "")
        c["note"] = _s.get("note", "")
        c["followup_at"] = _s.get("followup_at")
    # tampilkan SEMUA sesi (data fallback relatif sedikit), bukan hanya N teratas
    sessions = []
    for sid in session_ids:
        sessions.append({"session_id": sid, "hits": len(sess[sid]),
                         "ts_first": min(sess[sid]),
                         "intents": sorted(sess_intents.get(sid, set()))})
    sessions.sort(key=lambda s: -s["hits"])
    # Pemantauan pasca tindak-lanjut (jika sudah ditandai followup)
    monitor = candidate_followup_check(conn, phrase)
    return {
        "phrase": phrase,
        "occurrences": occ,
        "session_count": len(session_ids),
        "cooccurring_intents": co_list,
        "sessions": sessions,
        "monitor": monitor,
    }


def session_transcript(conn, session_id, limit=300):
    """Transkrip satu percakapan (urut waktu) untuk tombol 'Lihat percakapan'."""
    rows = conn.execute(
        "SELECT ts, day, user_phrase, bot_response, intent_name, is_fallback "
        "FROM interactions WHERE session_id=? ORDER BY ts ASC LIMIT ?",
        (session_id, int(limit)),
    ).fetchall()
    return [{
        "ts": r["ts"], "day": r["day"],
        "user_phrase": r["user_phrase"], "bot_response": r["bot_response"],
        "intent": r["intent_name"], "is_fallback": r["is_fallback"],
    } for r in rows]


def _now_z():
    return _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def set_candidate_status(conn, phrase, status, note="", user=""):
    """D6 — Tandai kandidat: '' (batal), 'skip', atau 'followup'.
    'followup' hanya PENANDA STATUS (training intent tetap manual di Dialogflow).
    Menyimpan followup_at pertama kali agar bisa dipantau sesudahnya."""
    norm = _norm_phrase(phrase)
    status = (status or "").strip().lower()
    if status not in ("skip", "followup", ""):
        status = ""
    now = _now_z()
    prev = conn.execute(
        "SELECT followup_at FROM candidate_status WHERE phrase_norm=?", (norm,)
    ).fetchone()
    if status == "followup":
        followup_at = (prev["followup_at"] if prev and prev["followup_at"] else now)
    else:
        followup_at = (prev["followup_at"] if prev else None)
    conn.execute(
        "INSERT INTO candidate_status"
        "(phrase_norm,phrase,status,note,updated_at,updated_by,followup_at) "
        "VALUES(?,?,?,?,?,?,?) "
        "ON CONFLICT(phrase_norm) DO UPDATE SET "
        "phrase=excluded.phrase, status=excluded.status, note=excluded.note, "
        "updated_at=excluded.updated_at, updated_by=excluded.updated_by, "
        "followup_at=excluded.followup_at",
        (norm, phrase, status, note or "", now, user or "", followup_at),
    )
    conn.commit()
    return {"ok": True, "phrase": phrase, "status": status,
            "updated_at": now, "followup_at": followup_at}


def get_candidate_statuses(conn, phrases=None):
    if phrases:
        norms = list({_norm_phrase(p) for p in phrases})
        if not norms:
            return {}
        qmarks = ",".join("?" * len(norms))
        rows = conn.execute(
            "SELECT * FROM candidate_status WHERE phrase_norm IN (" + qmarks + ")",
            norms,
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM candidate_status").fetchall()
    out = {}
    for r in rows:
        out[r["phrase_norm"]] = {
            "phrase": r["phrase"], "status": r["status"] or "",
            "note": r["note"] or "", "updated_at": r["updated_at"],
            "updated_by": r["updated_by"] or "", "followup_at": r["followup_at"],
        }
    return out


def set_intent_status(conn, intent, status, note="", user=""):
    # D6 - status tindak lanjut untuk INTENT (pecahan topik), bukan per frasa.
    intent = (intent or "").strip()
    if not intent:
        return {"ok": False, "error": "intent kosong."}
    status = (status or "").strip().lower()
    if status not in ("skip", "followup", ""):
        status = ""
    now = _now_z()
    prev = conn.execute("SELECT followup_at FROM intent_status WHERE intent_name=?", (intent,)).fetchone()
    if status == "followup":
        followup_at = (prev["followup_at"] if prev and prev["followup_at"] else now)
    else:
        followup_at = (prev["followup_at"] if prev else None)
    conn.execute(
        "INSERT INTO intent_status"
        "(intent_name,status,note,updated_at,updated_by,followup_at) "
        "VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(intent_name) DO UPDATE SET "
        "status=excluded.status, note=excluded.note, "
        "updated_at=excluded.updated_at, updated_by=excluded.updated_by, "
        "followup_at=excluded.followup_at",
        (intent, status, note or "", now, user or "", followup_at),
    )
    conn.commit()
    return {"ok": True, "intent": intent, "status": status, "updated_at": now, "followup_at": followup_at}


def get_intent_statuses(conn, intents=None):
    if intents:
        names = list({(i or "").strip() for i in intents if (i or "").strip()})
        if not names:
            return {}
        qmarks = ",".join("?" * len(names))
        rows = conn.execute("SELECT * FROM intent_status WHERE intent_name IN (" + qmarks + ")", names).fetchall()
    else:
        rows = conn.execute("SELECT * FROM intent_status").fetchall()
    out = {}
    for r in rows:
        out[r["intent_name"]] = {"status": r["status"] or "", "note": r["note"] or "", "updated_at": r["updated_at"], "updated_by": r["updated_by"] or "", "followup_at": r["followup_at"]}
    return out


def candidate_followup_check(conn, phrase):
    """Pantau apakah frasa MASIH jatuh ke fallback SETELAH ditandai 'followup'."""
    norm = _norm_phrase(phrase)
    row = conn.execute(
        "SELECT status, followup_at FROM candidate_status WHERE phrase_norm=?", (norm,)
    ).fetchone()
    if not row or not row["followup_at"]:
        return {"followed_up": False}
    fu = row["followup_at"]
    fu_day = fu[:10]
    frows = conn.execute(
        "SELECT user_phrase FROM interactions WHERE is_fallback=1 AND day >= ?",
        (fu_day,),
    ).fetchall()
    after = sum(1 for r in frows if _norm_phrase(r["user_phrase"]) == norm)
    return {
        "followed_up": True, "status": row["status"] or "",
        "followup_at": fu, "still_fallback_since": after,
        "resolved": (after == 0),
    }
