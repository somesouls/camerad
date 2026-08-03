# -*- coding: utf-8 -*-
"""
pustaka_stats.py
----------------
Pencatatan & pelaporan STATISTIK PEMAKAIAN PUSTAKA pengetahuan analis.

Setiap kali sebuah entri pustaka benar-benar dipakai untuk menyusun konteks
analisis/chat (hasil match() di knowledge_ctx), pemakaiannya dicatat di sini
sehingga tim tahu entri mana yang paling sering / jarang berguna.

Pustaka yang dilacak:
    glosarium     -> Glosarium Istilah Pajak (glossary_db)
    disambiguasi  -> Pustaka Disambiguasi (disambig_db)
    intentmap     -> Peta Intent & Maksud Analis / kebijakan (intentmap_db.match)
    katalog       -> Katalog Intent - Deskripsi AI (intentmap_db.match_catalog)

Memakai DB analitik yang sama (analytics_db.connect) agar satu file DB. Semua
operasi aman-gagal: pemanggil membungkusnya sehingga pencatatan tak pernah
mengganggu alur analisis/chat.
"""
import datetime as _dt
import analytics_db as adb

PUSTAKA_LABELS = {
    "glosarium": "Glosarium Pajak",
    "disambiguasi": "Disambiguasi",
    "intentmap": "Peta Intent",
    "katalog": "Katalog Intent",
}
PUSTAKA_ORDER = ("glosarium", "disambiguasi", "intentmap", "katalog")


def connect(db_path=None):
    return adb.connect(db_path) if db_path is not None else adb.connect()


def _now():
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def init_db(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS pustaka_pemakaian ("
        "pustaka TEXT NOT NULL, entry_id TEXT NOT NULL, label TEXT DEFAULT '', "
        "dipakai INTEGER DEFAULT 0, terakhir_dipakai TEXT, "
        "PRIMARY KEY (pustaka, entry_id))"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pp_pustaka ON pustaka_pemakaian(pustaka)")
    conn.commit()
    return conn


def log_hits(conn, pustaka, entries):
    """Naikkan counter pemakaian. `entries` = iterable of (entry_id, label)."""
    if not entries:
        return 0
    init_db(conn)
    now = _now()
    n = 0
    for eid, label in entries:
        eid = (str(eid) if eid is not None else "").strip()
        if not eid:
            continue
        conn.execute(
            "INSERT INTO pustaka_pemakaian (pustaka, entry_id, label, dipakai, terakhir_dipakai) "
            "VALUES (?, ?, ?, 1, ?) "
            "ON CONFLICT(pustaka, entry_id) DO UPDATE SET "
            "dipakai = dipakai + 1, terakhir_dipakai = excluded.terakhir_dipakai, "
            "label = CASE WHEN excluded.label != '' THEN excluded.label ELSE label END",
            (pustaka, eid, (label or "").strip(), now),
        )
        n += 1
    conn.commit()
    return n


def stats(conn, top_n=8):
    """Ringkasan per pustaka: total entri tercatat, total pemakaian, & top entri."""
    init_db(conn)
    out = {}
    for p in PUSTAKA_ORDER:
        total_entri = conn.execute(
            "SELECT COUNT(*) FROM pustaka_pemakaian WHERE pustaka=?", (p,)
        ).fetchone()[0]
        total_hit = conn.execute(
            "SELECT COALESCE(SUM(dipakai), 0) FROM pustaka_pemakaian WHERE pustaka=?", (p,)
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT entry_id, label, dipakai, terakhir_dipakai FROM pustaka_pemakaian "
            "WHERE pustaka=? ORDER BY dipakai DESC, terakhir_dipakai DESC LIMIT ?",
            (p, int(top_n)),
        ).fetchall()
        top = [
            {
                "entry_id": r["entry_id"],
                "label": r["label"] or r["entry_id"],
                "dipakai": int(r["dipakai"] or 0),
                "terakhir_dipakai": r["terakhir_dipakai"],
            }
            for r in rows
        ]
        out[p] = {
            "label": PUSTAKA_LABELS.get(p, p),
            "total_entri": int(total_entri or 0),
            "total_hit": int(total_hit or 0),
            "top": top,
        }
    return out


def usage_map(conn, pustaka):
    """{entry_id: dipakai} untuk memperkaya daftar entri suatu pustaka."""
    init_db(conn)
    rows = conn.execute(
        "SELECT entry_id, dipakai FROM pustaka_pemakaian WHERE pustaka=?", (pustaka,)
    ).fetchall()
    return {r["entry_id"]: int(r["dipakai"] or 0) for r in rows}


if __name__ == "__main__":
    c = init_db(connect())
    log_hits(c, "glosarium", [("efin", "EFIN"), ("npwp", "NPWP"), ("efin", "EFIN")])
    import json as _json
    print(_json.dumps(stats(c), ensure_ascii=False, indent=2))
