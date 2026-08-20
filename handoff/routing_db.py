# -*- coding: utf-8 -*-
"""handoff_routing_db.py — Tabel routing/perutean layanan (handoff) chatbot RAG.

Sebagian intent bukan sekadar "informasi" melainkan LAYANAN/tindakan yang punya
beberapa kanal penyelesaian:
  - mandiri : self-service (mis. Lupa EFIN via email lupa.efin@pajak.go.id)
  - agent   : Live Chat Agent Kring Pajak 1500200 (Sen-Jum 08.00-16.00 WIB)
  - kpp     : harus ke Kantor Pelayanan Pajak (verifikasi tatap muka)

Prinsip: pertanyaan INFORMASI/normatif tetap dijawab RAG (agent pun hanya
menjawab normatif). Perutean/handoff hanya relevan untuk intent LAYANAN yang
tercatat di tabel ini. Intent yang TIDAK ada di tabel => murni dijawab RAG.

Mengapa tabel TERPISAH (bukan menambah kolom ke katalog intent):
  - Katalog intent (intentmap_catalog) disinkronkan/di-regen dari Dialogflow
    (punya kolom sumber_status/soft_deleted) => kolom kustom rawan tertimpa.
  - Perutean hanya menyangkut SEGELINTIR intent => tabel kecil & mudah dirawat.

Tabel `handoff_routing`:
  id                INTEGER PK
  top_intent        TEXT  -- nama top-intent (mis. "Lupa EFIN"); UNIQUE
  pemicu            TEXT  -- JSON array frasa pemicu (mis. ["lupa efin"])
  kanal_mandiri     INTEGER DEFAULT 0
  instruksi_mandiri TEXT
  prasyarat_mandiri TEXT
  kanal_agent       INTEGER DEFAULT 0
  kanal_kpp         INTEGER DEFAULT 0
  instruksi_kpp     TEXT
  catatan           TEXT
  aktif             INTEGER DEFAULT 1
  created_at, updated_at TEXT

Env: PIPELINE_ROUTING_DB_FILE atau PIPELINE_RAG_DB_FILE atau 'rag.db'
(berbagi berkas dengan profil RAG). Gagal-anggun: fungsi baca mengembalikan
nilai kosong bila DB bermasalah.
"""
import os
import re
import json
import sqlite3

DB_FILE = (os.environ.get("PIPELINE_ROUTING_DB_FILE")
           or os.environ.get("PIPELINE_RAG_DB_FILE") or "rag.db")


def connect():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass
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
        CREATE TABLE IF NOT EXISTS handoff_routing (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            top_intent        TEXT NOT NULL,
            pemicu            TEXT,
            kanal_mandiri     INTEGER DEFAULT 0,
            instruksi_mandiri TEXT,
            prasyarat_mandiri TEXT,
            kanal_agent       INTEGER DEFAULT 0,
            kanal_kpp         INTEGER DEFAULT 0,
            instruksi_kpp     TEXT,
            catatan           TEXT,
            aktif             INTEGER DEFAULT 1,
            created_at        TEXT DEFAULT (datetime('now')),
            updated_at        TEXT DEFAULT (datetime('now'))
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_handoff_intent ON handoff_routing(top_intent);
        CREATE INDEX IF NOT EXISTS idx_handoff_aktif ON handoff_routing(aktif);
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


def _json_list(v):
    try:
        x = json.loads(v) if v else []
        return [str(t).strip() for t in x if str(t).strip()] if isinstance(x, list) else []
    except Exception:
        return []


def _norm_list(v):
    if isinstance(v, list):
        arr = [str(t).strip() for t in v if str(t).strip()]
    else:
        arr = [t.strip() for t in re.split(r"[,\n;]+", str(v or "")) if t.strip()]
    out = []
    for t in arr:
        if t.lower() not in [x.lower() for x in out]:
            out.append(t)
    return out


def _truthy(v):
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on", "ya")


# --------------------------------------------------------------- tulis
def upsert(data, conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        top_intent = str(data.get("top_intent") or "").strip()
        if not top_intent:
            raise ValueError("field 'top_intent' wajib diisi")
        pemicu = json.dumps(_norm_list(data.get("pemicu")), ensure_ascii=False)
        km = 1 if _truthy(data.get("kanal_mandiri")) else 0
        ka = 1 if _truthy(data.get("kanal_agent")) else 0
        kk = 1 if _truthy(data.get("kanal_kpp")) else 0
        im = str(data.get("instruksi_mandiri") or "").strip() or None
        pm = str(data.get("prasyarat_mandiri") or "").strip() or None
        ik = str(data.get("instruksi_kpp") or "").strip() or None
        cat = str(data.get("catatan") or "").strip() or None
        aktif = 0 if str(data.get("aktif")) in ("0", "false", "False", "no") else 1
        idv = data.get("id")
        if idv:
            conn.execute(
                "UPDATE handoff_routing SET top_intent=?, pemicu=?, kanal_mandiri=?, "
                "instruksi_mandiri=?, prasyarat_mandiri=?, kanal_agent=?, kanal_kpp=?, "
                "instruksi_kpp=?, catatan=?, aktif=?, updated_at=datetime('now') WHERE id=?",
                (top_intent, pemicu, km, im, pm, ka, kk, ik, cat, aktif, int(idv)),
            )
            new_id = int(idv)
        else:
            cur = conn.execute(
                "INSERT INTO handoff_routing(top_intent,pemicu,kanal_mandiri,"
                "instruksi_mandiri,prasyarat_mandiri,kanal_agent,kanal_kpp,"
                "instruksi_kpp,catatan,aktif) VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(top_intent) DO UPDATE SET pemicu=excluded.pemicu,"
                "kanal_mandiri=excluded.kanal_mandiri,"
                "instruksi_mandiri=excluded.instruksi_mandiri,"
                "prasyarat_mandiri=excluded.prasyarat_mandiri,"
                "kanal_agent=excluded.kanal_agent,kanal_kpp=excluded.kanal_kpp,"
                "instruksi_kpp=excluded.instruksi_kpp,catatan=excluded.catatan,"
                "aktif=excluded.aktif,updated_at=datetime('now')",
                (top_intent, pemicu, km, im, pm, ka, kk, ik, cat, aktif),
            )
            new_id = int(cur.lastrowid or 0)
        conn.commit()
        return {"id": new_id}
    finally:
        if own:
            conn.close()


def delete(id_, conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        conn.execute("DELETE FROM handoff_routing WHERE id=?", (int(id_),))
        conn.commit()
        return {"dihapus": 1}
    finally:
        if own:
            conn.close()


# --------------------------------------------------------------- baca
def get(id_, conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        r = conn.execute("SELECT * FROM handoff_routing WHERE id=?", (int(id_),)).fetchone()
        d = dict(r) if r else None
        if d:
            d["pemicu_list"] = _json_list(d.get("pemicu"))
        return d
    finally:
        if own:
            conn.close()


def list_all(q="", conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        q = (q or "").strip()
        if q:
            rows = conn.execute(
                "SELECT * FROM handoff_routing WHERE top_intent LIKE ? OR "
                "COALESCE(pemicu,'') LIKE ? ORDER BY top_intent",
                ("%" + q + "%", "%" + q + "%"),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM handoff_routing ORDER BY top_intent"
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["pemicu_list"] = _json_list(d.get("pemicu"))
            out.append(d)
        return out
    finally:
        if own:
            conn.close()


def stats(conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        n = conn.execute("SELECT COUNT(*) FROM handoff_routing").fetchone()[0] or 0
        na = conn.execute("SELECT COUNT(*) FROM handoff_routing WHERE COALESCE(aktif,1)=1").fetchone()[0] or 0
        return {"total": int(n), "aktif": int(na)}
    finally:
        if own:
            conn.close()


# --------------------------------------------------------------- pencocokan
def _has_phrase(text_low, phrase):
    p = (phrase or "").strip().lower()
    if not p:
        return False
    return re.search(r"(?<![0-9a-z])" + re.escape(p) + r"(?![0-9a-z])", text_low) is not None


def match_routing(question, conn=None):
    """Cari entri perutean paling cocok utk `question`.

    Kecocokan hanya lewat frasa `pemicu` (sinyal kuat & deterministik) agar tidak
    salah mengarahkan. Frasa lebih PANJANG dianggap lebih spesifik => menang.
    Kembalikan dict baris atau None. Gagal-anggun.
    """
    ql = (question or "").lower().strip()
    if not ql:
        return None
    own = conn is None
    try:
        conn = conn or init_db(connect())
    except Exception:
        return None
    try:
        try:
            rows = conn.execute(
                "SELECT * FROM handoff_routing WHERE COALESCE(aktif,1)=1"
            ).fetchall()
        except Exception:
            rows = []
        best, best_score = None, 0
        for r in rows:
            d = dict(r)
            score = 0
            for ph in _json_list(d.get("pemicu")):
                if _has_phrase(ql, ph) and len(ph) > score:
                    score = len(ph)
            if score > best_score:
                best, best_score = d, score
        return best
    finally:
        if own:
            try:
                conn.close()
            except Exception:
                pass


def guidance_text(row):
    """Rangkai blok instruksi perutean utk disisipkan ke system_prompt."""
    if not row:
        return ""
    ti = str(row.get("top_intent") or "").strip()
    km = int(row.get("kanal_mandiri") or 0)
    ka = int(row.get("kanal_agent") or 0)
    kk = int(row.get("kanal_kpp") or 0)
    lines = []
    lines.append("=== PANDUAN PERUTEAN LAYANAN ===")
    lines.append("Pertanyaan pengguna berkaitan dengan layanan: %s." % ti)
    lines.append(
        "Setelah menjawab informasi secara normatif dari KONTEKS INTERNAL, "
        "sampaikan opsi kanal penyelesaian berikut sesuai kondisi pengguna:")
    if km:
        s = "- Mandiri (self-service): " + (
            str(row.get("instruksi_mandiri") or "").strip()
            or "arahkan pengguna untuk menyelesaikan sendiri.")
        pr = str(row.get("prasyarat_mandiri") or "").strip()
        if pr:
            s += " Prasyarat: " + pr + "."
        lines.append(s)
    if ka:
        lines.append(
            "- Live Chat Agent (Kring Pajak 1500200, Senin-Jumat 08.00-16.00 "
            "WIB): tawarkan HANYA bila pengguna tidak dapat atau menolak "
            "menyelesaikan secara mandiri. Konfirmasi lebih dulu, misalnya "
            "'Apakah Kakak ingin saya hubungkan ke petugas Live Chat?', dan "
            "hubungkan hanya bila pengguna setuju.")
    if kk:
        s = "- Kantor Pelayanan Pajak (KPP): " + (
            str(row.get("instruksi_kpp") or "").strip()
            or "arahkan pengguna ke KPP terdaftar.")
        lines.append(s)
    lines.append("Aturan perutean:")
    if km:
        lines.append("- Utamakan kanal mandiri bila memungkinkan agar beban agent/KPP berkurang.")
    if ka:
        lines.append("- Jangan menghubungkan ke petugas Live Chat tanpa konfirmasi pengguna terlebih dahulu.")
    else:
        lines.append("- Layanan ini TIDAK dilayani Live Chat Agent. JANGAN menawarkan hubungan ke petugas Live Chat; arahkan ke kanal mandiri/KPP yang sesuai.")
    lines.append("- Jangan memberi kepastian Ya/Tidak atau Benar/Salah untuk kasus spesifik; sampaikan ketentuan secara normatif dan arahkan ke kanal yang tepat untuk verifikasi data pribadi.")
    cat = str(row.get("catatan") or "").strip()
    if cat:
        lines.append("Catatan: " + cat)
    lines.append("=== AKHIR PANDUAN PERUTEAN ===")
    return "\n".join(lines)


# --------------------------------------------------------------- seed
_DEFAULT_SEED = [
    {
        "top_intent": "Perubahan Data",
        "pemicu": ["perubahan data", "ubah data", "ganti data", "mengubah data",
                   "update data npwp", "ubah data profil", "ubah alamat",
                   "pindah alamat", "ganti alamat"],
        "kanal_mandiri": 1,
        "instruksi_mandiri": ("Bila Wajib Pajak dapat login ke Coretax/DJP Online, "
                              "sebagian perubahan data dapat dilakukan sendiri "
                              "melalui menu profil pada aplikasi."),
        "prasyarat_mandiri": "memiliki akses dan dapat login Coretax/DJP Online",
        "kanal_agent": 1,
        "kanal_kpp": 1,
        "instruksi_kpp": ("Untuk perubahan data yang memerlukan verifikasi atau "
                          "dokumen pendukung, Wajib Pajak dapat mengunjungi KPP terdaftar."),
        "catatan": "",
    },
    {
        "top_intent": "Lupa EFIN",
        "pemicu": ["lupa efin", "lupa e-fin", "efin lupa", "efin hilang",
                   "kehilangan efin"],
        "kanal_mandiri": 1,
        "instruksi_mandiri": ("Wajib Pajak dapat mengajukan layanan lupa EFIN secara "
                              "mandiri melalui email resmi lupa.efin@pajak.go.id "
                              "sesuai ketentuan/persyaratan yang berlaku."),
        "prasyarat_mandiri": "memiliki email dan dokumen identitas untuk verifikasi",
        "kanal_agent": 1,
        "kanal_kpp": 1,
        "instruksi_kpp": ("Wajib Pajak juga dapat memperoleh kembali EFIN dengan "
                          "datang langsung ke KPP terdaftar membawa identitas."),
        "catatan": "",
    },
    {
        "top_intent": "Aktivasi EFIN",
        "pemicu": ["aktivasi efin", "aktivasi e-fin", "permohonan efin",
                   "belum punya efin", "cara mendapatkan efin", "minta efin"],
        "kanal_mandiri": 0,
        "instruksi_mandiri": "",
        "prasyarat_mandiri": "",
        "kanal_agent": 0,
        "kanal_kpp": 1,
        "instruksi_kpp": ("Aktivasi EFIN memerlukan verifikasi tatap muka sehingga "
                          "Wajib Pajak harus mengajukan ke KPP terdaftar sesuai ketentuan."),
        "catatan": "Layanan ini tidak dapat diselesaikan melalui Live Chat Agent.",
    },
    {
        "top_intent": "Konfirmasi NPWP",
        "pemicu": ["konfirmasi npwp", "konfirmasi status npwp", "cek status npwp",
                   "validasi npwp", "konfirmasi wajib pajak"],
        "kanal_mandiri": 0,
        "instruksi_mandiri": "",
        "prasyarat_mandiri": "",
        "kanal_agent": 1,
        "kanal_kpp": 1,
        "instruksi_kpp": "Konfirmasi NPWP juga dapat dilakukan di KPP terdaftar.",
        "catatan": "",
    },
]


def seed_default(conn):
    """Isi entri default bila tabel masih kosong (idempoten)."""
    try:
        n = conn.execute("SELECT COUNT(*) FROM handoff_routing").fetchone()[0] or 0
    except Exception:
        return
    if n:
        return
    for row in _DEFAULT_SEED:
        try:
            upsert(row, conn=conn)
        except Exception:
            pass
