# -*- coding: utf-8 -*-
"""sosmed_knowledge.py — Otak menu Sosmed yang diringkas: FAQ + Deteksi Gap.

Tujuan (sesuai arahan): menu Sosmed cukup jadi DATABASE FAQ untuk memastikan bot
punya pengetahuan yang cukup. Fokusnya:
  1. Kelompokkan pertanyaan customer (root 'pertanyaan') jadi klaster FAQ.
  2. Urutkan by frekuensi -> pertanyaan yang lagi TREND.
  3. Cek tiap klaster ke pengetahuan bot: Peta Intent + Training Phrase
     (katalog Dialogflow) + opsional SBERT semantik. Bila TIDAK ada intent yang
     cocok -> tandai GAP ('belum ada intent').
  4. Bawa draf jawaban dari balasan resmi (answer_text) sebagai bibit FAQ.

Hanya STDLIB + reuse modul pengetahuan yang ada (intentmap_db, knowledge_semantic).
Gagal-anggun: bila modul pengetahuan tak tersedia, deteksi gap tetap jalan
(menganggap belum ada intent) tanpa meng-crash menu.

Smoke test: `python3 sosmed_knowledge.py` -> cetak SOSMED_KNOWLEDGE_SMOKE_OK.
"""
import re

import sosmed_db as sdb

try:
    import intentmap_db as _imdb
except Exception:      # pragma: no cover
    _imdb = None

try:
    import knowledge_semantic as _ksem
except Exception:      # pragma: no cover
    _ksem = None


_STOP = set("""yang dan di ke dari untuk pada dengan atau ini itu ada apa
bagaimana gimana kenapa mengapa kah min admin kak pak bu mohon tolong ya
nya saya aku kami kita mau ingin bisa tidak gak ga nggak sudah belum juga
kalau jika saja lagi kok dong sih halo hai the a an is to of for kalo klo
misal misalnya sih nih dll dsb utk yg gmn""".split())


def _keywords(text, k=8):
    if not text:
        return []
    words = re.findall(r"[a-zA-Z]{3,}", text.lower())
    freq = {}
    for w in words:
        if w in _STOP:
            continue
        freq[w] = freq.get(w, 0) + 1
    return [w for w, _ in sorted(freq.items(), key=lambda x: -x[1])[:k]]


# ---------------------------------------------------------------------------
# Koneksi pengetahuan bot (lazy, di-cache)
# ---------------------------------------------------------------------------
_IMCONN = None


def _im_conn():
    global _IMCONN
    if _imdb is None:
        return None
    if _IMCONN is not None:
        return _IMCONN
    try:
        c = _imdb.connect()
        _imdb.init_db(c)
        _imdb.init_catalog(c)
        _IMCONN = c
    except Exception:
        _IMCONN = None
    return _IMCONN


def check_intent(query, imconn=None, use_semantic=True, min_len=3):
    """Apakah pengetahuan bot sudah punya intent yang cocok untuk `query`?

    Menggabungkan (sesuai pilihan user: KEDUANYA):
      - Peta Intent   : intentmap_db.match()          (kebijakan analis)
      - Training Phrase: intentmap_db.match_catalog()  (katalog Dialogflow)
      - opsional Semantik: knowledge_semantic.semantic_match() (SBERT)
    Return {ada_intent, intents:[...], sumber:[...], semantik:bool}.
    """
    q = (query or "").strip()
    if len(q) < min_len:
        return {"ada_intent": False, "intents": [], "sumber": [], "semantik": False}
    imconn = imconn if imconn is not None else _im_conn()
    intents, sumber = [], []
    if imconn is not None and _imdb is not None:
        try:
            for m in _imdb.match(imconn, q, limit=3):
                if m.get("intent"):
                    intents.append(m["intent"]); sumber.append("peta_intent")
        except Exception:
            pass
        try:
            for m in _imdb.match_catalog(imconn, q, limit=3):
                if m.get("intent"):
                    intents.append(m["intent"]); sumber.append("training_phrase")
        except Exception:
            pass
    semantik = False
    if use_semantic and _ksem is not None:
        try:
            if _ksem.is_available():
                res = _ksem.semantic_match(q, per_lib_limit=2)
                for lib in ("intentmap", "katalog"):
                    for m in (res.get(lib) or []):
                        if m.get("intent"):
                            intents.append(m["intent"]); sumber.append("semantik")
                            semantik = True
        except Exception:
            pass
    intents = list(dict.fromkeys(intents))[:5]
    return {"ada_intent": bool(intents), "intents": intents,
            "sumber": sorted(set(sumber)), "semantik": semantik}


# ---------------------------------------------------------------------------
# FAQ + Deteksi Gap Pengetahuan
# ---------------------------------------------------------------------------
def knowledge_gap(conn, platform="", range_="all", start="", end="",
                  min_count=1, use_semantic=True, limit=200, imconn=None):
    """Klaster pertanyaan (per topik) -> FAQ + status gap intent.

    Tiap klaster:
      topik, jumlah (trending), belum_terjawab, contoh (3), keywords,
      draf_jawaban (dari balasan resmi terverifikasi manusia bila ada),
      ada_intent (bool), intents (nama), sumber (peta_intent/training_phrase/semantik),
      gap (bool = belum ada intent yang tepat).
    """
    pairs = sdb.faq_pairs(conn, platform=platform, range_=range_,
                          start=start, end=end, limit=5000)["pairs"]
    clusters = {}
    for p in pairs:
        key = p.get("topik") or "(lainnya)"
        c = clusters.setdefault(key, {
            "topik": key, "jumlah": 0, "terjawab": 0, "belum_terjawab": 0,
            "contoh": [], "kw": {}, "draf_jawaban": "", "draf_dari": "",
            "engagement": 0, "_rep": "", "_rep_like": -1,
        })
        c["jumlah"] += 1
        if p.get("status") == "terjawab":
            c["terjawab"] += 1
        elif p.get("status") == "belum_terjawab":
            c["belum_terjawab"] += 1
        like = int(p.get("like_count") or 0) + int(p.get("reply_count") or 0)
        c["engagement"] += like
        q = (p.get("pertanyaan") or "").strip()
        if q and len(c["contoh"]) < 3:
            c["contoh"].append(q[:240])
        # representative = pertanyaan dengan engagement tertinggi
        if q and like > c["_rep_like"]:
            c["_rep"] = q; c["_rep_like"] = like
        # draf jawaban: pakai balasan resmi pertama yang tersedia
        if not c["draf_jawaban"] and (p.get("jawaban_draf") or "").strip():
            c["draf_jawaban"] = p["jawaban_draf"].strip()[:1000]
            c["draf_dari"] = p.get("answered_by") or ""
        for kw in _keywords(q):
            c["kw"][kw] = c["kw"].get(kw, 0) + 1

    imconn = imconn if imconn is not None else _im_conn()
    out = []
    for c in clusters.values():
        if c["jumlah"] < min_count:
            continue
        top_kw = [k for k, _ in sorted(c["kw"].items(), key=lambda x: -x[1])[:8]]
        # query cek intent = topik + representative + kata kunci teratas
        probe = " ".join(filter(None, [
            c["topik"] if c["topik"] != "(lainnya)" else "",
            c["_rep"], " ".join(top_kw[:5])]))
        chk = check_intent(probe, imconn=imconn, use_semantic=use_semantic)
        out.append({
            "topik": c["topik"], "jumlah": c["jumlah"],
            "terjawab": c["terjawab"], "belum_terjawab": c["belum_terjawab"],
            "engagement": c["engagement"],
            "contoh": c["contoh"], "keywords": top_kw,
            "draf_jawaban": c["draf_jawaban"], "draf_dari": c["draf_dari"],
            "ada_intent": chk["ada_intent"], "intents": chk["intents"],
            "sumber": chk["sumber"], "semantik": chk["semantik"],
            "gap": (not chk["ada_intent"]),
        })
    # urut: TREND dulu (jumlah), lalu engagement
    out.sort(key=lambda x: (x["jumlah"], x["engagement"]), reverse=True)
    out = out[:limit]

    total_q = sum(c["jumlah"] for c in out)
    gap_q = sum(c["jumlah"] for c in out if c["gap"])
    n_gap = sum(1 for c in out if c["gap"])
    return {
        "ok": True,
        "clusters": out,
        "ringkasan": {
            "total_klaster": len(out),
            "klaster_gap": n_gap,
            "total_pertanyaan": total_q,
            "pertanyaan_tanpa_intent": gap_q,
            "pct_tercakup": (round(100.0 * (total_q - gap_q) / total_q, 1)
                             if total_q else 0.0),
            "knowledge_source": _source_status(),
        },
        "sla_minutes": sdb.sla_minutes(),
    }


def _source_status():
    st = {"peta_intent": _imdb is not None, "training_phrase": _imdb is not None,
          "semantik": False}
    if _ksem is not None:
        try:
            st["semantik"] = bool(_ksem.is_available())
        except Exception:
            st["semantik"] = False
    return st


# ===========================================================================
# Smoke test
# ===========================================================================
if __name__ == "__main__":
    import os, tempfile
    dbf = os.path.join(tempfile.mkdtemp(), "sk_test.db")
    os.environ["SOSMED_DB_FILE"] = dbf
    os.environ["SOSMED_OFFICIAL_HANDLES"] = "kring_pajak"
    c = sdb.init_db(sdb.connect())
    sample = [
        {"platform": "x", "id": "1", "conversation_id": "t1",
         "author_handle": "a", "created_at": "2026-08-04T10:00:00Z",
         "text": "min saya lupa EFIN gimana reset nya", "like_count": 5},
        {"platform": "x", "id": "2", "conversation_id": "t1", "in_reply_to_id": "1",
         "author_handle": "kring_pajak", "created_at": "2026-08-04T10:20:00Z",
         "text": "Silakan hubungi Kring Pajak 1500200 untuk reset EFIN."},
        {"platform": "x", "id": "3", "conversation_id": "t2",
         "author_handle": "b", "created_at": "2026-08-04T11:00:00Z",
         "text": "lupa efin juga min tolong", "like_count": 2},
        {"platform": "x", "id": "4", "conversation_id": "t3",
         "author_handle": "d", "created_at": "2026-08-05T09:00:00Z",
         "text": "cara daftar coretax gimana ya min baru banget"},
    ]
    sdb.ingest_items(c, sample, source="import")

    # tanpa modul intent (fallback) -> semua gap
    res = knowledge_gap(c, use_semantic=False, imconn=None)
    assert res["ok"], res
    efin = [x for x in res["clusters"] if x["topik"] == "Lupa EFIN"][0]
    assert efin["jumlah"] == 2, efin            # trending: 2 pertanyaan lupa efin
    assert efin["draf_jawaban"].startswith("Silakan"), efin["draf_jawaban"]

    # dengan intent map dummy: 'Lupa EFIN' punya intent, 'coretax' belum
    class _Dummy:
        def match(self, conn, q, limit=3):
            return [{"intent": "EFIN"}] if "efin" in q.lower() else []
        def match_catalog(self, conn, q, limit=4):
            return [{"intent": "EFIN"}] if "efin" in q.lower() else []
    globals()["_imdb"] = _Dummy()   # patch running module's global
    res2 = knowledge_gap(c, use_semantic=False, imconn=object())
    m = {x["topik"]: x for x in res2["clusters"]}
    assert m["Lupa EFIN"]["ada_intent"] is True and m["Lupa EFIN"]["gap"] is False, m["Lupa EFIN"]
    cx = [x for x in res2["clusters"] if "coretax" in " ".join(x["keywords"])]
    assert cx and cx[0]["gap"] is True, cx    # coretax = gap (belum ada intent)
    assert res2["ringkasan"]["klaster_gap"] >= 1, res2["ringkasan"]

    c.close()
    print("SOSMED_KNOWLEDGE_SMOKE_OK")
