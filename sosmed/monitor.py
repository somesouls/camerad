# -*- coding: utf-8 -*-
"""sosmed/monitor.py — Lapisan data Halaman "Pengawasan SPV" (Sosmed).

Menyajikan HANYA komentar/tweet UTAMA dari warga (bukan akun resmi) + kolom
kurasi manual Supervisor, tanpa mengubah skema inti di sosmed/db.py (kolom SPV
ditambah via ALTER TABLE idempoten & fail-soft).

DETEKSI "UTAMA" (independen dari item_type inti db.py, sesuai arahan SPV):
  - UTAMA    : item non-resmi yang INDUKNYA akun RESMI atau TANPA induk (root).
               (mis. di X: user membalas balasan resmi = interaksi/utama BARU.)
  - TAMBAHAN : item non-resmi yang induknya sesama non-resmi → DIGABUNG ke utama
               terdekat (mis. tweet D "menambahkan info" atas tweet C).
  - RESMI    : semua item akun resmi (kandidat jawaban).

JAWABAN sebuah utama = balasan RESMI paling awal yang "menuruni" utama tsb
(mendaki in_reply_to dari tiap balasan resmi sampai bertemu utama/tambahannya).
Tanggal jawab & selisih (jam+menit) dihitung otomatis dari sini.

KURASI MANUAL SPV (menang atas deteksi otomatis):
  - spv_answered    : 'ya' / 'belum' / 'itd' (Interaksi Tidak Dijawab) / ''.
  - spv_answered_at : tanggal jawab versi SPV.
  - spv_answer_link : permalink komentar jawaban (diisi lewat PICKER di modal
                      mata untuk kasus jawaban tak-berelasi, mis. IG mention off).
  - crm_url         : tautan tiket/CRM.
  - spv_note        : catatan bebas (mis. "dibalas admin via HP").
  - spv_updated_by / spv_updated_at : jejak audit.

Catatan penting: agar akun resmi tiap platform dikenali (mis. IG
'kringpajak1500200'), daftarkan semua handle resmi di env SOSMED_OFFICIAL_HANDLES.
Deteksi resmi di modul ini memakai kolom is_official ATAU pencocokan handle live,
sehingga cukup set env + restart tanpa perlu impor ulang.
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

# Pilihan status jawab (override manual SPV).
_ANSWER_CHOICES = ("ya", "belum", "itd")


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


# ---------------------------------------------------------------------------
# Deteksi akun resmi (robust: kolom is_official ATAU handle live)
# ---------------------------------------------------------------------------
def _off_set():
    try:
        return set(sdb.official_handles())
    except Exception:
        return set()


def _is_off(row, off):
    if row.get("is_official") == 1:
        return True
    h = (row.get("author_handle") or "").strip().lstrip("@").lower()
    return bool(h and h in off)


# ---------------------------------------------------------------------------
# Rekonstruksi satu conversation -> peran (utama/tambahan/resmi) + peta jawaban
# ---------------------------------------------------------------------------
def _classify_conv(rows, off):
    """rows: semua baris satu (platform, conversation_id), urut waktu ASC.
    Kembalikan (role, main_of, officials_of):
      role[ext]        : 'main' | 'addition' | 'official'
      main_of[ext]     : ext utama untuk item 'main'/'addition'
      officials_of[m]  : list baris resmi (jawaban) untuk utama m, urut waktu ASC
    """
    by_ext = {r["external_id"]: r for r in rows if r.get("external_id")}
    role = {}
    main_of = {}
    for r in rows:
        ext = r.get("external_id")
        if not ext:
            continue
        if _is_off(r, off):
            role[ext] = "official"
            continue
        parent = by_ext.get(r.get("in_reply_to_id") or "")
        if parent is None or _is_off(parent, off):
            role[ext] = "main"
            main_of[ext] = ext
        else:
            role[ext] = "addition"

    def _climb_main(start_ext):
        cur = by_ext.get(start_ext)
        seen = set()
        while cur is not None and cur.get("external_id") not in seen:
            e = cur.get("external_id")
            seen.add(e)
            if role.get(e) == "main":
                return e
            if _is_off(cur, off):
                return None
            cur = by_ext.get(cur.get("in_reply_to_id") or "")
        return None

    for r in rows:
        ext = r.get("external_id")
        if role.get(ext) == "addition":
            m = _climb_main(ext)
            if m:
                main_of[ext] = m
            else:
                # tambahan yatim (induk hilang dari tarikan) -> jadikan utama.
                role[ext] = "main"
                main_of[ext] = ext

    officials_of = {}
    for r in rows:
        if not _is_off(r, off):
            continue
        cur = by_ext.get(r.get("in_reply_to_id") or "")
        seen = set()
        target = None
        while cur is not None and cur.get("external_id") not in seen:
            e = cur.get("external_id")
            seen.add(e)
            rl = role.get(e)
            if rl == "main":
                target = e
                break
            if rl == "addition":
                target = main_of.get(e)
                break
            cur = by_ext.get(cur.get("in_reply_to_id") or "")
        if target:
            officials_of.setdefault(target, []).append(r)
    for m, lst in officials_of.items():
        lst.sort(key=lambda x: (x.get("created_at") or "", x.get("id") or 0))
    return role, main_of, officials_of


def _eff_status(row, auto_answered):
    """Status jawab EFEKTIF: override manual SPV menang atas deteksi otomatis.
    Kembalikan salah satu dari 'ya' / 'belum' / 'itd'."""
    spv = (row.get("spv_answered") or "").strip().lower()
    if spv in _ANSWER_CHOICES:
        return spv
    return "ya" if auto_answered else "belum"


def _candidate_convs(conn, norm_plat, s, e, q):
    where = ["1=1"]
    params = []
    if norm_plat:
        where.append("platform=?")
        params.append(norm_plat)
    if s:
        where.append("substr(created_at,1,10)>=?")
        params.append(s)
    if e:
        where.append("substr(created_at,1,10)<=?")
        params.append(e)
    if q:
        where.append("(text LIKE ? OR author_name LIKE ? OR author_handle LIKE ?)")
        params += ["%" + q + "%"] * 3
    sql = ("SELECT DISTINCT platform, conversation_id FROM sosmed_items WHERE "
           + " AND ".join(where))
    return [(r[0], r[1]) for r in conn.execute(sql, params).fetchall() if r[1]]


def monitor_list(conn, platform="", range_="all", start="", end="",
                 answered="", q="", limit=500):
    """Daftar komentar/tweet UTAMA warga + kolom kurasi SPV (halaman Pengawasan).

    Rentang tanggal diterapkan pada tanggal UTAMA itu sendiri (mis. tweet C yang
    menyusul di hari berikutnya tampil di hari-C, bukan hari root). Setiap baris
    membawa turunan efektif: eff_status ('ya'/'belum'/'itd'), eff_answered_at,
    dan eff_gap_s (detik) untuk kolom Selisih (dirender jam+menit di UI).
    """
    ensure_review_columns(conn)
    off = _off_set()
    norm_plat = sdb._norm_platform(platform) if platform else ""
    rng = (range_ or "all").lower()
    if rng == "custom":
        s, e = (start or None), (end or start or None)
    else:
        s, e = sdb.resolve_range(rng)

    convs = _candidate_convs(conn, norm_plat, s, e, q)
    want = (answered or "").strip().lower()
    ql = (q or "").lower()
    out = []
    for (plat, conv) in convs:
        rows = [dict(x) for x in conn.execute(
            "SELECT * FROM sosmed_items WHERE platform=? AND conversation_id=? "
            "ORDER BY datetime(created_at) ASC, id ASC", (plat, conv)).fetchall()]
        if not rows:
            continue
        role, main_of, officials_of = _classify_conv(rows, off)
        for r in rows:
            ext = r.get("external_id")
            if role.get(ext) != "main":
                continue
            day = (r.get("created_at") or "")[:10]
            if s and day < s:
                continue
            if e and day > e:
                continue
            if ql:
                hay = " ".join([str(r.get("text") or ""),
                                str(r.get("author_name") or ""),
                                str(r.get("author_handle") or "")]).lower()
                if ql not in hay:
                    continue
            offs = officials_of.get(ext, [])
            auto_ans = bool(offs)
            auto_at = offs[0].get("created_at") if offs else ""
            auto_by = offs[0].get("author_handle") if offs else ""
            eff = _eff_status(r, auto_ans)
            if eff == "ya":
                eff_at = (r.get("spv_answered_at") or auto_at or "")
            elif eff == "itd":
                eff_at = ""
            else:
                eff_at = (r.get("spv_answered_at") or "")
            gap = None
            if eff == "ya" and eff_at:
                try:
                    gap = sdb._resp_seconds(r.get("created_at"), eff_at)
                except Exception:
                    gap = None
            n_add = sum(1 for x in rows
                        if role.get(x.get("external_id")) == "addition"
                        and main_of.get(x.get("external_id")) == ext)
            if want in _ANSWER_CHOICES and eff != want:
                continue
            out.append({
                "id": r.get("id"),
                "platform": plat,
                "conversation_id": conv,
                "external_id": ext,
                "author_handle": r.get("author_handle"),
                "author_name": r.get("author_name"),
                "created_at": r.get("created_at"),
                "text": r.get("text"),
                "permalink": r.get("permalink"),
                "topik": r.get("topik"),
                "auto_answered": 1 if auto_ans else 0,
                "auto_answered_at": auto_at,
                "auto_answered_by": auto_by,
                "crm_url": r.get("crm_url") or "",
                "spv_answered": r.get("spv_answered") or "",
                "spv_answered_at": r.get("spv_answered_at") or "",
                "spv_answer_link": r.get("spv_answer_link") or "",
                "spv_note": r.get("spv_note") or "",
                "eff_status": eff,
                "eff_answered_at": eff_at,
                "eff_gap_s": gap,
                "n_additions": n_add,
                "n_official": len(offs),
            })
    out.sort(key=lambda x: (x.get("created_at") or ""), reverse=True)
    out = out[:int(limit)]
    try:
        platforms = [x[0] for x in conn.execute(
            "SELECT DISTINCT platform FROM sosmed_items WHERE platform!='' "
            "ORDER BY platform").fetchall()]
    except Exception:
        platforms = []
    return {"ok": True, "items": out, "total": len(out), "platforms": platforms}


def monitor_thread(conn, item_id):
    """Klaster interaksi satu UTAMA (untuk modal ikon mata):
      - cluster : utama + tambahan + jawaban resmi terkait (urut waktu, berdepth
                  untuk menampilkan cabang di X; IG/TikTok = induk + anak).
      - official_candidates : semua komentar RESMI di conversation/post yang sama
                  (untuk PICKER penautan jawaban manual saat tak ada relasi).
    """
    ensure_review_columns(conn)
    off = _off_set()
    base = conn.execute("SELECT * FROM sosmed_items WHERE id=?",
                        (int(item_id),)).fetchone()
    if not base:
        return {"ok": False, "error": "Item tidak ditemukan."}
    base = dict(base)
    plat, conv = base.get("platform"), base.get("conversation_id")
    rows = [dict(x) for x in conn.execute(
        "SELECT * FROM sosmed_items WHERE platform=? AND conversation_id=? "
        "ORDER BY datetime(created_at) ASC, id ASC", (plat, conv)).fetchall()]
    role, main_of, officials_of = _classify_conv(rows, off)
    by_ext = {r["external_id"]: r for r in rows if r.get("external_id")}
    ext = base.get("external_id")
    if role.get(ext) != "main":
        ext = main_of.get(ext, ext)

    def _depth(r):
        d = 0
        cur = r
        seen = set()
        while cur is not None and cur.get("external_id") not in seen and d < 12:
            e = cur.get("external_id")
            if e == ext:
                return d
            seen.add(e)
            cur = by_ext.get(cur.get("in_reply_to_id") or "")
            d += 1
        return d

    off_for_main = officials_of.get(ext, [])
    off_ids = {x.get("id") for x in off_for_main}
    members = []
    main_row = by_ext.get(ext)
    if main_row:
        members.append(main_row)
    for r in rows:
        e = r.get("external_id")
        if e == ext:
            continue
        if role.get(e) == "addition" and main_of.get(e) == ext:
            members.append(r)
        elif r.get("id") in off_ids:
            members.append(r)
    members.sort(key=lambda x: (x.get("created_at") or "", x.get("id") or 0))
    cluster = []
    for r in members:
        e = r.get("external_id")
        cluster.append({
            "id": r.get("id"),
            "external_id": e,
            "is_official": 1 if _is_off(r, off) else 0,
            "role": role.get(e),
            "author_handle": r.get("author_handle"),
            "author_name": r.get("author_name"),
            "created_at": r.get("created_at"),
            "text": r.get("text"),
            "permalink": r.get("permalink"),
            "depth": _depth(r),
        })
    cands = []
    for r in rows:
        if _is_off(r, off):
            cands.append({
                "id": r.get("id"),
                "external_id": r.get("external_id"),
                "created_at": r.get("created_at"),
                "text": r.get("text"),
                "permalink": r.get("permalink"),
                "author_handle": r.get("author_handle"),
            })
    cands.sort(key=lambda x: (x.get("created_at") or ""), reverse=True)
    main_dict = by_ext.get(ext, base)
    return {"ok": True, "platform": plat, "conversation_id": conv,
            "main_id": main_dict.get("id"), "main_external_id": ext,
            "main_text": main_dict.get("text"), "cluster": cluster,
            "official_candidates": cands}


def update_review(conn, item_id, fields, user=None):
    """Perbarui kolom kurasi SPV untuk satu item. fields = subset EDITABLE_FIELDS.

    Nilai kosong/None disimpan sebagai NULL. spv_answered dipaksa ke salah satu
    dari {'ya','belum','itd',''}. Selalu memperbarui jejak audit spv_updated_by/at.
    """
    ensure_review_columns(conn)
    sets, params = [], []
    for k in EDITABLE_FIELDS:
        if k in (fields or {}):
            v = fields[k]
            if isinstance(v, str):
                v = v.strip()
            if k == "spv_answered" and v not in _ANSWER_CHOICES:
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
