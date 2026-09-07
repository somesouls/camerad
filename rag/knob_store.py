# -*- coding: utf-8 -*-
"""rag/knob_store.py — Penyimpan \"knob\" RAG per-profil (Tahap 4 #1).

Tujuan: menyediakan SATU sumber kebenaran untuk nilai knob retrieval yang bisa
diubah dari halaman admin (/rag-harness) TANPA redeploy, dengan urutan
prioritas:

    nilai tersimpan (per-profil)  >  variabel lingkungan (env)  >  default kode

Profil yang dikenal: \"agent\" dan \"chatbot\" (lihat PROFILES). Setiap profil bisa
punya nilai knob sendiri; bila sebuah profil belum menyetel knob, nilainya jatuh
ke env lalu ke default kode — jadi perilaku lama (env-only) TETAP sama persis
selama belum ada yang menyetel lewat panel.

CATATAN PENTING: modul ini HANYA menyimpan & menyelesaikan nilai. Ia BELUM
menyambung ke pipeline (mis. rag.calibration). Penyambungan runtime dilakukan di
langkah terpisah agar bisa diuji bertahap. Semua getter GAGAL-ANGGUN: bila DB
tak tersedia, nilai jatuh ke env/default.

Stdlib saja; tanpa f-string (mengikuti gaya rag/golden_db.py).

Uji cepat:
    python rag/knob_store.py --show --profile agent
    python rag/knob_store.py --set RAG_MIN_COS=0.61 --profile agent
    python rag/knob_store.py --show --profile agent
    python rag/knob_store.py --show --profile chatbot
    python rag/knob_store.py --clear RAG_MIN_COS --profile agent
"""
import os
import sqlite3
import argparse
import datetime as _dt

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # paket rag/ -> root repo


# Profil RAG yang dikenal panel.
PROFILES = ("agent", "chatbot")

# Registry knob: type = "float" | "bool"; default = nilai bila store & env kosong.
# 'env' = nama variabel lingkungan yang jadi fallback tingkat kedua.
KNOBS = {
    "RAG_MIN_COS": {
        "type": "float", "default": 0.0, "env": "RAG_MIN_COS",
        "label": "Ambang cosine gerbang (peraturan + intent)",
        "help": "0 = gerbang mati. Naikkan agar lebih sering abstain saat relevansi rendah.",
    },
    "RAG_QA_MIN_COS": {
        "type": "float", "default": 0.50, "env": "RAG_QA_MIN_COS",
        "label": "Ambang cosine kemiripan pertanyaan (Q2Q)",
        "help": "Ambang retrieval Q2Q historis AWE/Sosmed.",
    },
    "RAG_VALIDITY_GUARD": {
        "type": "bool", "default": False, "env": "RAG_VALIDITY_GUARD",
        "label": "Penjaga status hukum (validity guard)",
        "help": "Tandai/tolak kutipan peraturan yang dicabut/diubah.",
    },
    "RAG_INGEST_DEDUP": {
        "type": "bool", "default": False, "env": "RAG_INGEST_DEDUP",
        "label": "Deduplikasi saat ingest",
        "help": "Cegah duplikasi unit peraturan saat pemasukan data.",
    },
    "RAG_NOMOR_PIN": {
        "type": "bool", "default": True, "env": "RAG_NOMOR_PIN",
        "label": "Pin nomor peraturan eksak",
        "help": "Prioritaskan peraturan ber-nomor sama saat query menyebut nomor.",
    },
    "RAG_CITATION_FETCH": {
        "type": "bool", "default": True, "env": "RAG_CITATION_FETCH",
        "label": "Fetch sitasi nomor+pasal (kebal gate)",
        "help": "Bila query menyebut nomor peraturan + pasal eksplisit, tarik ISI pasal itu langsung dari DB lintas semua status (tampilkan + penanda status + penerus).",
    },
}

_TRUE = ("1", "true", "yes", "on", "ya", "aktif")
_FALSE = ("0", "false", "no", "off", "tidak", "nonaktif")


def _db_path():
    return os.environ.get("PIPELINE_KNOB_DB_FILE") or os.path.join(_BASE_DIR, "knob.db")


def connect():
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS rag_knob ("
        " profile TEXT NOT NULL,"
        " name TEXT NOT NULL,"
        " value TEXT,"
        " updated_at TEXT,"
        " PRIMARY KEY (profile, name))"
    )
    conn.commit()
    return conn


def _now():
    return _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _norm_profile(profile):
    p = (profile or "").strip().lower()
    if p not in PROFILES:
        raise ValueError("profil tak dikenal: %r (pilih dari %s)"
                         % (profile, ", ".join(PROFILES)))
    return p


def _norm_name(name):
    n = (name or "").strip()
    if n not in KNOBS:
        raise ValueError("knob tak dikenal: %r (pilih dari %s)"
                         % (name, ", ".join(sorted(KNOBS))))
    return n


def _coerce(name, raw):
    """Ubah string mentah -> nilai bertipe sesuai registry. None bila gagal."""
    if raw is None:
        return None
    spec = KNOBS.get(name) or {}
    typ = spec.get("type")
    s = str(raw).strip()
    if s == "":
        return None
    try:
        if typ == "float":
            return float(s)
        if typ == "bool":
            low = s.lower()
            if low in _TRUE:
                return True
            if low in _FALSE:
                return False
            return None
    except Exception:
        return None
    return s


def _to_store_str(name, value):
    """Normalkan nilai apa pun (untuk disimpan) jadi string kanonik."""
    spec = KNOBS.get(name) or {}
    typ = spec.get("type")
    if typ == "bool":
        if isinstance(value, bool):
            return "1" if value else "0"
        v = _coerce(name, value)
        if v is None:
            raise ValueError("nilai bool tak valid utk %s: %r" % (name, value))
        return "1" if v else "0"
    if typ == "float":
        v = _coerce(name, value)
        if v is None:
            raise ValueError("nilai angka tak valid utk %s: %r" % (name, value))
        return repr(float(v))
    return str(value)


def get_raw(profile, name, conn=None):
    """Nilai string tersimpan utk (profile,name), atau None bila tak ada."""
    p = _norm_profile(profile)
    n = _norm_name(name)
    own = False
    if conn is None:
        conn = init_db(connect())
        own = True
    try:
        row = conn.execute(
            "SELECT value FROM rag_knob WHERE profile=? AND name=?", (p, n)
        ).fetchone()
        return row["value"] if row is not None else None
    finally:
        if own:
            conn.close()


def set_knob(profile, name, value, conn=None):
    p = _norm_profile(profile)
    n = _norm_name(name)
    sval = _to_store_str(n, value)
    own = False
    if conn is None:
        conn = init_db(connect())
        own = True
    try:
        conn.execute(
            "INSERT INTO rag_knob (profile, name, value, updated_at) "
            "VALUES (?,?,?,?) ON CONFLICT(profile,name) DO UPDATE SET "
            "value=excluded.value, updated_at=excluded.updated_at",
            (p, n, sval, _now()),
        )
        conn.commit()
        return {"profile": p, "name": n, "value": sval}
    finally:
        if own:
            conn.close()


def clear_knob(profile, name, conn=None):
    p = _norm_profile(profile)
    n = _norm_name(name)
    own = False
    if conn is None:
        conn = init_db(connect())
        own = True
    try:
        cur = conn.execute(
            "DELETE FROM rag_knob WHERE profile=? AND name=?", (p, n))
        conn.commit()
        return {"profile": p, "name": n, "deleted": cur.rowcount}
    finally:
        if own:
            conn.close()


def _env_value(name):
    spec = KNOBS.get(name) or {}
    envname = spec.get("env") or name
    if envname in os.environ:
        return _coerce(name, os.environ.get(envname))
    return None


def resolve(profile, name, conn=None):
    """Nilai bertipe efektif dgn precedence store>env>default. GAGAL-ANGGUN."""
    try:
        n = _norm_name(name)
    except Exception:
        return None
    p = None
    try:
        p = _norm_profile(profile)
    except Exception:
        p = None
    if p is not None:
        try:
            v = _coerce(n, get_raw(p, n, conn=conn))
            if v is not None:
                return v
        except Exception:
            pass
    try:
        v = _env_value(n)
        if v is not None:
            return v
    except Exception:
        pass
    return KNOBS[n]["default"]


def resolve_verbose(profile, name, conn=None):
    """Seperti resolve() tetapi kembalikan {value, source}."""
    n = _norm_name(name)
    p = None
    try:
        p = _norm_profile(profile)
    except Exception:
        p = None
    if p is not None:
        raw = None
        try:
            raw = get_raw(p, n, conn=conn)
        except Exception:
            raw = None
        v = _coerce(n, raw)
        if v is not None:
            return {"value": v, "source": "store"}
    v = None
    try:
        v = _env_value(n)
    except Exception:
        v = None
    if v is not None:
        return {"value": v, "source": "env"}
    return {"value": KNOBS[n]["default"], "source": "default"}


def all_effective(profile, conn=None):
    """{name: {value, source, type, default, label, help}} utk seluruh knob."""
    own = False
    if conn is None:
        conn = init_db(connect())
        own = True
    try:
        out = {}
        for n in KNOBS:
            rv = resolve_verbose(profile, n, conn=conn)
            spec = KNOBS[n]
            out[n] = {
                "value": rv["value"],
                "source": rv["source"],
                "type": spec["type"],
                "default": spec["default"],
                "label": spec.get("label", n),
                "help": spec.get("help", ""),
            }
        return out
    finally:
        if own:
            conn.close()


def list_store(conn=None):
    own = False
    if conn is None:
        conn = init_db(connect())
        own = True
    try:
        rows = conn.execute(
            "SELECT profile, name, value, updated_at FROM rag_knob "
            "ORDER BY profile, name").fetchall()
        return [dict(r) for r in rows]
    finally:
        if own:
            conn.close()


def _fmt(v):
    if isinstance(v, bool):
        return "on" if v else "off"
    return str(v)


def _cli(argv=None):
    ap = argparse.ArgumentParser(
        description="Knob store RAG per-profil (Tahap 4 #1).")
    ap.add_argument("--profile", default="agent", help="agent|chatbot")
    ap.add_argument("--show", action="store_true", help="tampilkan knob efektif")
    ap.add_argument("--set", dest="set_kv", default=None, help="NAMA=NILAI")
    ap.add_argument("--clear", dest="clear_name", default=None, help="NAMA")
    ap.add_argument("--list-store", action="store_true",
                    help="daftar nilai tersimpan")
    args = ap.parse_args(argv)

    if args.set_kv:
        if "=" not in args.set_kv:
            print("format --set salah, pakai NAMA=NILAI")
            return 2
        k, v = args.set_kv.split("=", 1)
        res = set_knob(args.profile, k.strip(), v.strip())
        print("[knob_store] set %s/%s = %s"
              % (res["profile"], res["name"], res["value"]))

    if args.clear_name:
        res = clear_knob(args.profile, args.clear_name.strip())
        print("[knob_store] clear %s/%s (dihapus=%d) -> kembali ke env/default"
              % (res["profile"], res["name"], res["deleted"]))

    if args.list_store:
        rows = list_store()
        if not rows:
            print("(store kosong)")
        for r in rows:
            print("- %s/%s = %s (updated %s)"
                  % (r["profile"], r["name"], r["value"], r["updated_at"]))

    if args.show or not (args.set_kv or args.clear_name or args.list_store):
        eff = all_effective(args.profile)
        print("== KNOB EFEKTIF (profil=%s) ==" % args.profile)
        for n in sorted(eff):
            e = eff[n]
            print("- %-20s = %-8s [%s]  (default=%s)  %s"
                  % (n, _fmt(e["value"]), e["source"], _fmt(e["default"]),
                     e["label"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
