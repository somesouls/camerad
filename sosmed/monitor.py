# -*- coding: utf-8 -*-
"""sosmed/monitor.py — Lapisan data Halaman \"Pengawasan SPV\" (Sosmed).

Menyajikan HANYA komentar/tweet UTAMA dari warga (bukan akun resmi) + kolom
kurasi manual Supervisor, tanpa mengubah skema inti di sosmed/db.py (kolom SPV
ditambah via ALTER TABLE idempoten & fail-soft).

DETEKSI \"UTAMA\" (independen dari item_type inti db.py, sesuai arahan SPV):
  - UTAMA    : item non-resmi yang INDUKNYA akun RESMI atau TANPA induk (root).
               (mis. di X: user membalas balasan resmi = interaksi/utama BARU.)
  - TAMBAHAN : item non-resmi yang induknya sesama non-resmi → DIGABUNG ke utama
               terdekat (mis. tweet D \"menambahkan info\" atas tweet C).
  - RESMI    : semua item akun resmi (kandidat jawaban).

NIMBRUNG (deteksi ringan, NON-DESTRUKTIF terhadap SLA):
  Sebuah TAMBAHAN yang penulisnya BERBEDA dari penulis UTAMA-nya dianggap
  \"nimbrung\" (warga lain yang menyela di utas warga lain). Secara default hanya
  DITANDAI (flag/badge + hitungan) dan TIDAK mengubah SLA — SLA IG/TikTok tetap
  dihitung dari komentar UTAMA. Namun SPV bisa MENGAWASI nimbrung sebagai baris
  tersendiri dengan mengaktifkan include_nimbrung=True (dari centangan di UI):
  saat itu tiap nimbrung tampil sebagai baris yang bisa dikurasi (status jawab,
  tgl jawab, dll.), dengan deteksi jawaban resmi yang MEMBALAS nimbrung itu
  langsung. Deteksi murni dari data yang sudah ada (author_handle + relasi
  in_reply_to), jadi mudah & aman.

JAWABAN sebuah utama = balasan RESMI paling awal yang \"menuruni\" utama tsb
(mendaki in_reply_to dari tiap balasan resmi sampai bertemu utama/tambahannya).
Tanggal jawab & selisih (jam+menit) dihitung otomatis dari sini.

PENAMAAN POSTINGAN: tiap (platform, conversation_id) bisa diberi nama bebas oleh
SPV (mis. \"P1 Lupa Kata Sandi\"), disimpan di tabel meta (sosmed_meta) dengan
kunci 'postlabel:<platform>:<conversation_id>' — tanpa mengubah skema inti.

KURASI MANUAL SPV (menang atas deteksi otomatis):
  - spv_answered    : 'ya' / 'belum' / 'itd' (Interaksi Tidak Dijawab) / ''.
  - spv_answered_at : tanggal jawab versi SPV.
  - spv_answer_link : permalink komentar jawaban (diisi lewat PICKER di modal
                      mata untuk kasus jawaban tak-berelasi, mis. IG mention off).
  - crm_url         : tautan tiket/CRM.
  - spv_note        : catatan bebas (mis. \"dibalas admin via HP\").
  - spv_updated_by / spv_updated_at : jejak audit.

Catatan penting: agar akun resmi tiap platform dikenali (mis. IG
'kringpajak1500200'), daftarkan semua handle resmi di env SOSMED_OFFICIAL_HANDLES.
Deteksi resmi di modul ini memakai kolom is_official ATAU pencocokan handle live,
sehingga cukup set env + restart tanpa perlu impor ulang.
"""
import re as _re

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
# Penamaan postingan (disimpan di sosmed_meta, tanpa ubah skema inti)
# ---------------------------------------------------------------------------
def _pl_key(plat, conv):
    return "postlabel:%s:%s" % ((plat or ""), (conv or ""))


def get_post_label(conn, plat, conv):
    try:
        return sdb.get_meta(conn, _pl_key(plat, conv)) or ""
    except Exception:
        return ""


def set_post_label(conn, plat, conv, label):
    """Simpan/hapus nama postingan. label kosong = hapus."""
    label = (label or "").strip()
    key = _pl_key(plat, conv)
    if label:
        try:
            sdb.set_meta(conn, key, label)
        except Exception:
            pass
    else:
        try:
            conn.execute("DELETE FROM sosmed_meta WHERE key=?", (key,))
            conn.commit()
        except Exception:
            pass
    return True


def _all_post_labels(conn):
    out = {}
    try:
        for r in conn.execute(
                "SELECT key, value FROM sosmed_meta WHERE key LIKE 'postlabel:%'"
        ).fetchall():
            out[r[0]] = r[1]
    except Exception:
        pass
    return out


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


def _handle(row):
    return (row.get("author_handle") or "").strip().lstrip("@").lower()


# ---------------------------------------------------------------------------
# Rekonstruksi satu conversation -> peran (utama/tambahan/resmi) + peta jawaban
# ---------------------------------------------------------------------------
def _classify_conv(rows, off):
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
        if parent is None:
            role[ext] = "main"
            main_of[ext] = ext
        elif _is_off(parent, off):
            # Warga membalas langsung ke akun resmi -> masuk Tambahan/Nimbrung
            role[ext] = "addition"
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
                # KUNCI PERBAIKAN: Jangan paksa jadi "main"!
                # Tetap jadikan "addition" agar masuk ke filter Nimbrung
                role[ext] = "addition"
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



def _officials_direct(rows, off, by_ext):
    """Untuk tiap balasan RESMI, cari leluhur NON-RESMI TERDEKAT (utama ATAU
    tambahan) lalu atribusikan ke situ. Dipakai mendeteksi jawaban yang membalas
    sebuah NIMBRUNG secara langsung. Kembalikan dict ext -> list resmi (ASC)."""
    direct = {}
    for r in rows:
        if not _is_off(r, off):
            continue
        cur = by_ext.get(r.get("in_reply_to_id") or "")
        seen = set()
        while cur is not None and cur.get("external_id") not in seen:
            e = cur.get("external_id")
            seen.add(e)
            if not _is_off(cur, off):
                direct.setdefault(e, []).append(r)
                break
            cur = by_ext.get(cur.get("in_reply_to_id") or "")
    for e, lst in direct.items():
        lst.sort(key=lambda x: (x.get("created_at") or "", x.get("id") or 0))
    return direct


def _is_nimbrung(row, ext_main, by_ext):
    """True bila TAMBAHAN ini membalas official langsung, atau ditulis warga yang BERBEDA dari penulis UTAMA-nya."""
    parent = by_ext.get(row.get("in_reply_to_id") or "")
    
    # Aturan Baru: Jika membalas langsung ke akun resmi -> Mutlak Nimbrung
    if parent and parent.get("is_official") == 1:
        return True
        
    main_row = by_ext.get(ext_main)
    if not main_row:
        return False
    ah = _handle(row)
    mh = _handle(main_row)
    return bool(ah and mh and ah != mh)


def _eff_status(row, auto_answered):
    """Status jawab EFEKTIF: override manual SPV menang atas deteksi otomatis.
    Kembalikan salah satu dari 'ya' / 'belum' / 'itd'."""
    spv = (row.get("spv_answered") or "").strip().lower()
    if spv in _ANSWER_CHOICES:
        return spv
    return "ya" if auto_answered else "belum"


def _post_url(plat, conv, rows):
    """URL representatif POSTINGAN (bukan komentar), diturunkan dari permalink item.

    - IG   : permalink komentar berbentuk .../p/<code>/c/<pk>/ -> ambil .../p/<code>/
    - TikTok: permalink sudah berupa URL video (.../video/<id>) -> pakai apa adanya.
    - X/lainnya: pakai permalink item paling awal (tautan tweet perwakilan).
    """
    plat = (plat or "").lower()
    perms = [(r.get("permalink") or "") for r in rows if r.get("permalink")]
    if plat == "ig":
        for p in perms:
            m = _re.search(r"(https?://[^/]*instagram\.com/(?:p|reel|tv)/[^/]+/)", p)
            if m:
                return m.group(1)
        return ""
    if plat == "tiktok":
        for p in perms:
            if "/video/" in p or "/photo/" in p:
                return p.split("?")[0]
        return ""
    return (perms[0].split("?")[0] if perms else "")


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


def _mk_row(r, plat, conv, role_name, is_nimbrung, offs, n_add, n_nimbrung,
            label, parent_handle=""):
    """Bangun satu baris keluaran monitor_list (utama atau nimbrung)."""
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
    return {
        "id": r.get("id"),
        "platform": plat,
        "conversation_id": conv,
        "external_id": r.get("external_id"),
        "role": role_name,
        "is_nimbrung": 1 if is_nimbrung else 0,
        "parent_handle": parent_handle or "",
        "post_label": label or "",
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
        "n_nimbrung": n_nimbrung,
        "n_official": len(offs),
    }


def monitor_list(conn, platform="", range_="all", start="", end="",
                 answered="", q="", include_nimbrung=False, limit=500):
    """Daftar komentar/tweet UTAMA warga + kolom kurasi SPV (halaman Pengawasan).

    Rentang tanggal diterapkan pada tanggal UTAMA itu sendiri (mis. tweet C yang
    menyusul di hari berikutnya tampil di hari-C, bukan hari root). Setiap baris
    membawa turunan efektif: eff_status ('ya'/'belum'/'itd'), eff_answered_at,
    dan eff_gap_s (detik) untuk kolom Selisih (dirender jam+menit di UI).

    Bila include_nimbrung=True, tiap komentar NIMBRUNG (tambahan dari warga lain)
    juga ditampilkan sebagai baris yang bisa dikurasi, dengan deteksi jawaban
    resmi yang membalasnya langsung. SLA komentar utama TIDAK berubah.
    """
    ensure_review_columns(conn)
    off = _off_set()
    labels = _all_post_labels(conn)
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
        by_ext = {r["external_id"]: r for r in rows if r.get("external_id")}
        direct = _officials_direct(rows, off, by_ext) if include_nimbrung else {}
        label = labels.get(_pl_key(plat, conv), "")

        def _passes(r):
            day = (r.get("created_at") or "")[:10]
            if s and day < s:
                return False
            if e and day > e:
                return False
            if ql:
                hay = " ".join([str(r.get("text") or ""),
                                str(r.get("author_name") or ""),
                                str(r.get("author_handle") or "")]).lower()
                if ql not in hay:
                    return False
            return True

        for r in rows:
            ext = r.get("external_id")
            rl = role.get(ext)
            if rl == "main":
                if not _passes(r):
                    continue
                offs = officials_of.get(ext, [])
                n_add = 0
                n_nimbrung = 0
                for x in rows:
                    xe = x.get("external_id")
                    if role.get(xe) == "addition" and main_of.get(xe) == ext:
                        n_add += 1
                        if _is_nimbrung(x, ext, by_ext):
                            n_nimbrung += 1
                row = _mk_row(r, plat, conv, "main", False, offs, n_add,
                              n_nimbrung, label)
                if want in _ANSWER_CHOICES and row["eff_status"] != want:
                    continue
                out.append(row)
            elif include_nimbrung and rl == "addition":
                m = main_of.get(ext)
                if not _is_nimbrung(r, m, by_ext):
                    continue
                if not _passes(r):
                    continue
                offs = direct.get(ext, [])
                main_row = by_ext.get(m)
                parent_handle = _handle(main_row) if main_row else ""
                row = _mk_row(r, plat, conv, "nimbrung", True, offs, 0, 0,
                              label, parent_handle=parent_handle)
                if want in _ANSWER_CHOICES and row["eff_status"] != want:
                    continue
                out.append(row)
    out.sort(key=lambda x: (x.get("created_at") or ""), reverse=True)
    out = out[:int(limit)]
    try:
        platforms = [x[0] for x in conn.execute(
            "SELECT DISTINCT platform FROM sosmed_items WHERE platform!='' "
            "ORDER BY platform").fetchall()]
    except Exception:
        platforms = []
    return {"ok": True, "items": out, "total": len(out), "platforms": platforms}


def monitor_posts(conn, platform="", range_="all", start="", end="", q="",
                  limit=300):
    """Ringkasan PER POSTINGAN (conversation) untuk verifikasi data tarikan.

    Tiap postingan membawa hitungan: total komentar ditarik, jumlah utama,
    tambahan, resmi, nimbrung, serta status jawab utama (belum/sudah/itd),
    tautan postingan, dan nama (label) postingan bila sudah diberi SPV.
    """
    ensure_review_columns(conn)
    off = _off_set()
    labels = _all_post_labels(conn)
    norm_plat = sdb._norm_platform(platform) if platform else ""
    rng = (range_ or "all").lower()
    if rng == "custom":
        s, e = (start or None), (end or start or None)
    else:
        s, e = sdb.resolve_range(rng)
    convs = _candidate_convs(conn, norm_plat, s, e, q)
    out = []
    for (plat, conv) in convs:
        rows = [dict(x) for x in conn.execute(
            "SELECT * FROM sosmed_items WHERE platform=? AND conversation_id=? "
            "ORDER BY datetime(created_at) ASC, id ASC", (plat, conv)).fetchall()]
        if not rows:
            continue
        role, main_of, officials_of = _classify_conv(rows, off)
        by_ext = {r["external_id"]: r for r in rows if r.get("external_id")}
        n_main = n_add = n_off = n_nimbrung = 0
        n_ya = n_belum = n_itd = 0
        times = [r.get("created_at") or "" for r in rows if r.get("created_at")]
        for r in rows:
            ext = r.get("external_id")
            rl = role.get(ext)
            if rl == "official":
                n_off += 1
            elif rl == "addition":
                n_add += 1
                if _is_nimbrung(r, main_of.get(ext), by_ext):
                    n_nimbrung += 1
            elif rl == "main":
                n_main += 1
                eff = _eff_status(r, bool(officials_of.get(ext)))
                if eff == "ya":
                    n_ya += 1
                elif eff == "itd":
                    n_itd += 1
                else:
                    n_belum += 1
        times.sort()
        out.append({
            "platform": plat,
            "conversation_id": conv,
            "post_url": _post_url(plat, conv, rows),
            "post_label": labels.get(_pl_key(plat, conv), ""),
            "n_items": len(rows),
            "n_main": n_main,
            "n_addition": n_add,
            "n_official": n_off,
            "n_nimbrung": n_nimbrung,
            "n_answered": n_ya,
            "n_unanswered": n_belum,
            "n_itd": n_itd,
            "first_at": times[0] if times else "",
            "last_at": times[-1] if times else "",
        })
    out.sort(key=lambda x: (x.get("last_at") or ""), reverse=True)
    out = out[:int(limit)]
    try:
        platforms = [x[0] for x in conn.execute(
            "SELECT DISTINCT platform FROM sosmed_items WHERE platform!='' "
            "ORDER BY platform").fetchall()]
    except Exception:
        platforms = []
    return {"ok": True, "posts": out, "total": len(out), "platforms": platforms}


def monitor_post(conn, platform, conversation_id):
    """SEMUA komentar satu postingan (verifikasi), diurutkan ala thread:
    tiap UTAMA diikuti tambahan + jawaban resmi terkaitnya. Tiap item membawa
    role, is_official, is_nimbrung, depth, dan main_external_id. Dirender penuh
    di halaman agar bisa dicari (Ctrl+F).
    """
    ensure_review_columns(conn)
    off = _off_set()
    plat = sdb._norm_platform(platform) if platform else ""
    rows = [dict(x) for x in conn.execute(
        "SELECT * FROM sosmed_items WHERE platform=? AND conversation_id=? "
        "ORDER BY datetime(created_at) ASC, id ASC",
        (plat, conversation_id)).fetchall()]
    if not rows:
        return {"ok": False, "error": "Postingan tidak ditemukan."}
    role, main_of, officials_of = _classify_conv(rows, off)
    by_ext = {r["external_id"]: r for r in rows if r.get("external_id")}

    def _mk(r, depth, main_ext):
        e = r.get("external_id")
        is_off = 1 if _is_off(r, off) else 0
        nb = 0
        if role.get(e) == "addition" and _is_nimbrung(r, main_ext, by_ext):
            nb = 1
        return {
            "id": r.get("id"),
            "external_id": e,
            "is_official": is_off,
            "role": role.get(e),
            "is_nimbrung": nb,
            "author_handle": r.get("author_handle"),
            "author_name": r.get("author_name"),
            "created_at": r.get("created_at"),
            "text": r.get("text"),
            "permalink": r.get("permalink"),
            "depth": depth,
            "main_external_id": main_ext,
        }

    used = set()
    items = []
    mains = [r for r in rows if role.get(r.get("external_id")) == "main"]
    mains.sort(key=lambda x: (x.get("created_at") or "", x.get("id") or 0))
    for m in mains:
        me = m.get("external_id")
        if me in used:
            continue
        used.add(me)
        items.append(_mk(m, 0, me))
        members = []
        off_ids = {x.get("id") for x in officials_of.get(me, [])}
        for r in rows:
            e = r.get("external_id")
            if e == me:
                continue
            if (role.get(e) == "addition" and main_of.get(e) == me) \
                    or r.get("id") in off_ids:
                members.append(r)
        members.sort(key=lambda x: (x.get("created_at") or "", x.get("id") or 0))
        for r in members:
            e = r.get("external_id")
            if e in used:
                continue
            used.add(e)
            items.append(_mk(r, 1, me))
    # sisa (yatim / tak terpetakan) supaya verifikasi benar-benar lengkap.
    for r in rows:
        e = r.get("external_id")
        if e and e not in used:
            used.add(e)
            items.append(_mk(r, 0, None))
    return {"ok": True, "platform": plat, "conversation_id": conversation_id,
            "post_url": _post_url(plat, conversation_id, rows),
            "post_label": get_post_label(conn, plat, conversation_id),
            "n_items": len(rows), "items": items}


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
        nb = 0
        if role.get(e) == "addition" and _is_nimbrung(r, ext, by_ext):
            nb = 1
        cluster.append({
            "id": r.get("id"),
            "external_id": e,
            "is_official": 1 if _is_off(r, off) else 0,
            "role": role.get(e),
            "is_nimbrung": nb,
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
