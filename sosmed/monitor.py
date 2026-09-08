# -*- coding: utf-8 -*-
"""sosmed/monitor.py — Lapisan data Halaman "Pengawasan SPV" (Sosmed).

Menyediakan kolom KURASI MANUAL bagi Supervisor di atas tabel sosmed_items,
tanpa mengubah skema inti di sosmed/db.py (kolom ditambah lewat ALTER TABLE
idempoten & fail-soft):

  - crm_url         : tautan tiket/CRM terkait pertanyaan (diisi manual).
  - spv_answered    : override manual status jawab ('ya' / 'belum' / '').
  - spv_answered_at : tanggal jawaban versi SPV (mis. dibalas admin via HP).
  - spv_answer_link : permalink komentar jawaban (mis. jawaban ditaruh di
                      komentar UTAMA/root utk kasus IG yang tak bisa mention).
  - spv_note        : catatan bebas SPV.
  - spv_updated_by / spv_updated_at : jejak audit perubahan.

Latar: di Instagram, bila pemilik akun penanya tidak mengizinkan mention untuk
semua akun, komentar TAK BISA dibalas via browser (agent) — admin harus membalas
dari HP. Akibatnya deteksi "terjawab" otomatis bisa meleset, sehingga SPV perlu
menandai/menautkan jawaban secara manual. Karena itu status jawab EFEKTIF =
override manual SPV bila ada, jika tidak baru memakai deteksi otomatis pairing.
"""
import sosmed.db as sdb

# (nama_kolom, tipe_sqlite)
_REVIEW_COLS = (
    ("crm_url", "TEXT"),
    ("spv_answered", "TEXT"),
    ("spv_answered_at", "TEXT"),
    ("spv_answer_link", "TEXT"),
    ("spv_note", "TEXT"),
    ("spv_updated_by", "TEXT"),
    ("spv_updated_at", "TEXT"),
)

# Field yang boleh diubah SPV lewat endpoint review.
EDITABLE_FIELDS = ("crm_url", "spv_answered", "spv_answered_at",
                   "spv_answer_link", "spv_note")


def ensure_review_columns(conn):
    """Tambahkan kolom kurasi SPV bila belum ada (idempoten, fail-soft)."""
    try:
        have = {r[1] for r in conn.execute(
            'PRAGMA table_info("sosmed_items")').fetchall()}
    except Exception:
        return
    changed = False
    for name, typ in _REVIEW_COLS:
        if name not in have:
            try:
                conn.execute('ALTER TABLE sosmed_items ADD COLUMN %s %s'
                             % (name, typ))
                changed = True
            except Exception:
                pass
    if changed:
        try:
            conn.commit()
        except Exception:
            pass


def _eff_answered(row):
    """Status jawab EFEKTIF: override manual SPV menang atas deteksi otomatis."""
    spv = (row.get("spv_answered") or "").strip().lower()
    if spv == "ya":
        return True
    if spv == "belum":
        return False
    return row.get("status") == "terjawab"


def monitor_list(conn, platform="", range_="all", start="", end="",
                 answered="", q="", limit=500):
    """Daftar PERTANYAAN warga + kolom kurasi SPV untuk halaman Pengawasan.

    Menghitung turunan efektif per baris:
      - eff_answered   : 1/0 (override manual > deteksi otomatis balasan resmi)
      - eff_answered_at: tanggal jawab efektif (spv_answered_at > answered_at)
      - eff_gap_s      : selisih waktu tanya -> jawab (detik) bila sudah dijawab
    """
    ensure_review_columns(conn)
    where = ["item_type='pertanyaan'"]
    params = []
    if platform:
        where.append("platform=?")
        params.append(sdb._norm_platform(platform))
    rng = (range_ or "all").lower()
    if rng == "custom":
        s, e = (start or None), (end or start or None)
    else:
        s, e = sdb.resolve_range(rng)
    if s:
        where.append("substr(created_at,1,10)>=?")
        params.append(s)
    if e:
        where.append("substr(created_at,1,10)<=?")
        params.append(e)
    if q:
        where.append("(text LIKE ? OR author_name LIKE ? OR author_handle LIKE ?)")
        params += ["%" + q + "%"] * 3
    sql = ("SELECT * FROM sosmed_items WHERE " + " AND ".join(where)
           + " ORDER BY datetime(created_at) DESC LIMIT ?")
    params.append(int(limit))
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    want = (answered or "").strip().lower()
    out = []
    for r in rows:
        ans = _eff_answered(r)
        ans_at = (r.get("spv_answered_at") or r.get("answered_at") or "")
        gap = None
        if ans and ans_at:
            try:
                gap = sdb._resp_seconds(r.get("created_at"), ans_at)
            except Exception:
                gap = None
        r["eff_answered"] = 1 if ans else 0
        r["eff_answered_at"] = ans_at
        r["eff_gap_s"] = gap
        if want == "ya" and not ans:
            continue
        if want == "belum" and ans:
            continue
        out.append(r)
    try:
        platforms = [x[0] for x in conn.execute(
            "SELECT DISTINCT platform FROM sosmed_items WHERE platform!='' "
            "ORDER BY platform").fetchall()]
    except Exception:
        platforms = []
    return {"ok": True, "items": out, "total": len(out), "platforms": platforms}


def update_review(conn, item_id, fields, user=None):
    """Perbarui kolom kurasi SPV untuk satu item. fields = subset EDITABLE_FIELDS.

    Nilai kosong/None disimpan sebagai NULL. spv_answered dipaksa ke salah satu
    dari {'ya','belum',''}. Selalu memperbarui jejak audit spv_updated_by/at.
    """
    ensure_review_columns(conn)
    sets, params = [], []
    for k in EDITABLE_FIELDS:
        if k in (fields or {}):
            v = fields[k]
            if isinstance(v, str):
                v = v.strip()
            if k == "spv_answered" and v not in ("ya", "belum"):
                v = ""
            sets.append("%s=?" % k)
            params.append(v if (v is not None and v != "") else None)
    if not sets:
        return False
    sets.append("spv_updated_by=?")
    params.append((user or "") or None)
    sets.append("spv_updated_at=?")
    try:
        params.append(sdb._jkt_now_iso())
    except Exception:
        params.append(None)
    params.append(int(item_id))
    conn.execute("UPDATE sosmed_items SET %s WHERE id=?" % ", ".join(sets), params)
    conn.commit()
    return True
