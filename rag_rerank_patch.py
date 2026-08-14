# -*- coding: utf-8 -*-
"""rag_rerank_patch.py — Tahap 5: reranker + query rewriting untuk retrieval.

Membungkus peraturan_db.search agar:
  1. Query DIPERLUAS (rag_rewrite.untuk_retrieval): kamus sinonim + rewriting AI
     istilah/pasal. Ekspansi ini kini dipakai HANYA untuk jalur LEKSIKAL
     (FTS5/LIKE) -> menutup jurang bahasa awam vs bahasa hukum pada pencocokan
     kata & akronim.
  2. Jalur SEMANTIK (vektor e5) memakai query ASLI yang natural -> vektor query
     tidak "terkotori" oleh karung kata kunci panjang hasil ekspansi. e5 dilatih
     untuk query natural, jadi memberi daftar sinonim + istilah formal yang
     panjang justru melemahkan sinyal semantiknya.
  3. Kandidat DINILAI ULANG (rag_reranker.rerank) dengan cross-encoder memakai
     query ASLI -> urutan relevansi jauh lebih akurat.

Pemisahan dense vs leksikal (poin 1 & 2) memperbaiki perilaku lama yang memberi
query SAMA (sudah diperluas) ke FTS DAN e5 sekaligus, sehingga retrieval semantik
ikut melemah. Kini tiap jalur memakai bentuk query yang paling cocok.

Dipasang lewat web_app.py (import) SETELAH rag_successor_patch dan SEBELUM
rag_calibration_patch, agar gerbang cosine (rag_calibration_patch) tetap menilai
kemiripan terhadap query ASLI: gate memanggil wrapper ini dengan query asli,
wrapper memperluas query HANYA untuk mengambil kandidat leksikal, lalu rerank
memakai query asli; gate menilai cosine query asli atas hasil.

Gagal-anggun: bila modul rewrite/reranker atau modelnya tak tersedia, atau
retrieval terpisah gagal, perilaku kembali seperti semula (hybrid FTS5 + e5 atas
query diperluas, dipotong k).
"""
import os

import peraturan_db as _pdb

try:
    import rag_rewrite as _rw
except Exception:            # pragma: no cover
    _rw = None
try:
    import rag_reranker as _rr
except Exception:            # pragma: no cover
    _rr = None

_orig_search = _pdb.search


def _pool_size(k):
    try:
        base = int(os.environ.get("RAG_RERANK_POOL", "30"))
    except Exception:
        base = 30
    return max(int(k or 10), base)


def _split_search(q_dense, q_lex, k, status_list, conn=None):
    """Tiru peraturan_db.search namun PISAHKAN query per jalur:
      * FTS5/LIKE  -> q_lex   (query yang sudah diperluas kamus + AI)
      * vektor e5  -> q_dense (query asli yang natural)
    Digabung RRF, difilter status, diurut skor, lalu dipotong k. Memakai fungsi
    internal peraturan_db (_fts_ids/_vec_ids/_rrf) agar logika retrieval inti
    tetap satu sumber kebenaran.
    """
    own = conn is None
    conn = conn or _pdb.init_db(_pdb.connect())
    try:
        q_dense = (q_dense or "").strip()
        q_lex = (q_lex or q_dense).strip() or q_dense
        if not q_dense:
            return []
        fts = _pdb._fts_ids(conn, q_lex)
        vec = _pdb._vec_ids(conn, q_dense)
        scores = _pdb._rrf(fts, vec)
        if not scores:
            return []
        id_ph = ",".join("?" for _ in scores)
        st = list(status_list) if status_list else []
        if st:
            st_ph = ",".join("?" for _ in st)
            rows = conn.execute(
                "SELECT * FROM peraturan_unit WHERE id IN (%s) AND status IN (%s)"
                % (id_ph, st_ph),
                (*scores.keys(), *st),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM peraturan_unit WHERE id IN (%s)" % id_ph,
                tuple(scores.keys()),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["skor"] = scores.get(d["id"], 0.0)
            out.append(d)
        out.sort(key=lambda x: -x["skor"])
        return out[:k]
    finally:
        if own:
            conn.close()


def _search_rerank(query, k=10, status_list=("berlaku",), conn=None):
    q = (query or "").strip()
    # (1) perluas query HANYA untuk jalur leksikal (kamus + AI). Query ASLI tetap
    #     dipakai untuk jalur semantik (e5) dan untuk rerank.
    q_lex = q
    if _rw is not None and q:
        try:
            q_lex = _rw.untuk_retrieval(q) or q
        except Exception:
            q_lex = q
    # (2) ambil pool lebih besar bila reranker aktif, agar ada ruang urut ulang.
    use_rr = False
    try:
        use_rr = _rr is not None and _rr.is_available()
    except Exception:
        use_rr = False
    ambil = _pool_size(k) if use_rr else k
    # (3) retrieval hibrida dengan pemisahan dense vs leksikal; gagal-anggun ke
    #     perilaku lama (dense & leksikal sama-sama memakai query diperluas).
    try:
        rows = _split_search(q, q_lex, ambil, status_list, conn=conn)
    except Exception:
        try:
            rows = _orig_search(q_lex, k=ambil, status_list=status_list, conn=conn)
        except Exception:
            rows = []
    if use_rr and rows:
        try:
            rows = _rr.rerank(q, rows, top_k=k)
        except Exception:
            rows = rows[:k]
    else:
        rows = rows[:k]
    return rows


_pdb.search = _search_rerank

try:
    print("[rag_rerank_patch] aktif (rerank=%s, rewrite=%s, dense/lex split=on)"
          % (_rr is not None, _rw is not None))
except Exception:
    pass


# --- Ekspansi 1-hop "pasal terkait" (cross-reference): dimuat DI SINI agar
#     dijamin berjalan SETELAH rag_successor_patch (yang menetapkan
#     _ctx_peraturan versi successor-tracing) tanpa perlu mengubah registry
#     import di web_app.py. rag_xref_patch membungkus rag_engine._ctx_peraturan
#     agar pasal yang DIRUJUK ("sebagaimana dimaksud dalam Pasal X") ikut ditarik
#     ke konteks. Patch sesudah ini (calibration/grounding) tidak menyentuh
#     _ctx_peraturan, jadi posisi ini aman. Fail-open + bisa dimatikan RAG_XREF=0.
try:
    import rag_xref_patch  # noqa: F401  (menerapkan patch saat diimpor)
except Exception as _e:      # pragma: no cover
    print("[rag_rerank_patch] rag_xref_patch gagal dimuat:", _e)
