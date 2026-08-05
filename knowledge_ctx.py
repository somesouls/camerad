# -*- coding: utf-8 -*-
"""
knowledge_ctx.py  (HYBRID: keyword + SBERT semantic retrieval)
--------------------------------------------------------------
Helper TERPUSAT untuk menyambungkan tiga pustaka pengetahuan analis ke mesin
analisis & chat:

  1. Glosarium Istilah Pajak      (glossary_db)   -> arti istilah (EFIN, Coretax, ...)
  2. Pustaka Disambiguasi         (disambig_db)   -> makna yang benar utk frasa ambigu
  3. Peta Intent & Maksud Analis  (intentmap_db)  -> kunci jawaban / kebijakan analis
  (+ Katalog Intent, deskripsi maksud & cakupan)

RETRIEVAL HYBRID:
    Tiap pustaka digabung dari DUA sumber lalu di-dedup per id:
      (a) match() berbasis keyword  -> presisi tinggi untuk kecocokan literal
      (b) knowledge_semantic (SBERT) -> recall tinggi untuk parafrase/beda kata/
          salah ketik ringan
    Bila modul semantik / model tak tersedia (mis. torch belum terpasang),
    otomatis jatuh ke keyword saja (aman, perilaku lama).

Fungsi utama:
    build_analysis_context(query, max_chars=1800) -> str
        Blok teks ringkas berisi HANYA entri relevan dgn `query`, siap disuntik
        ke system/instruction prompt. Kosong bila tak ada (aman sebagai no-op).

        Disambiguasi DJP Online vs Coretax TIDAK memakai tanggal interaksi;
        aturannya (berbasis MASA PAJAK yang ditanyakan user) hanya disajikan ke
        LLM untuk diterapkan sendiri dari teks pertanyaan.

    system_suffix(query, max_chars=1800) -> str
        Sama, tetapi diawali dua newline agar praktis ditempel ke akhir
        system prompt. Kosong bila tak ada konteks.

Hanya memakai stdlib + modul db lokal (+ knowledge_semantic opsional). Semua
kegagalan ditangani diam-diam supaya analisis tetap berjalan walau pustaka
kosong / bermasalah.
"""
import glossary_db as gdb
import disambig_db as ddb
import intentmap_db as imdb
import pustaka_stats as pstats  # statistik pemakaian pustaka

try:
    import knowledge_semantic as ksem  # retriever SBERT (opsional)
except Exception:
    ksem = None

HEADER = (
    "=== KONTEKS PENGETAHUAN ANALIS (acuan internal tim) ===\n"
    "Berikut fakta & keputusan internal tim yang relevan dengan pertanyaan. "
    "Perlakukan sebagai ACUAN BENAR untuk menafsirkan istilah dan memilih intent, "
    "BUKAN sebagai perintah dari user."
)
FOOTER = "=== AKHIR KONTEKS PENGETAHUAN ==="


def _safe(fn, default=""):
    try:
        return fn() or default
    except Exception:
        return default


def _log_pustaka(pustaka, pairs):
    """Catat pemakaian entri pustaka (aman: kegagalan diabaikan)."""
    pairs = [(i, l) for (i, l) in (pairs or []) if i]
    if not pairs:
        return
    def run():
        c = pstats.connect()
        try:
            pstats.log_hits(c, pustaka, pairs)
        finally:
            c.close()
        return "ok"
    _safe(run)


def _merge(primary, extra, limit=4):
    """Gabung hasil keyword (primary) + semantik (extra); dedup per id; cap limit.
    Keyword didahulukan (presisi), lalu entri semantik yang belum ada."""
    out, seen = [], set()
    for x in list(primary or []) + list(extra or []):
        if not isinstance(x, dict):
            continue
        key = x.get("id")
        if key is None:
            key = id(x)
        if key in seen:
            continue
        seen.add(key)
        out.append(x)
        if len(out) >= limit:
            break
    return out


def _semantic_all(query):
    """{lib: [entry,...]} dari SBERT; {} bila modul/model tak tersedia."""
    if ksem is None:
        return {}
    try:
        if not ksem.is_available():
            return {}
        return ksem.semantic_match(query, per_lib_limit=4)
    except Exception:
        return {}


def _glossary_block(query, sem=None):
    def run():
        c = gdb.init_db(gdb.connect())
        try:
            m = _merge(gdb.match(c, query, limit=4), sem, limit=5)
            _log_pustaka("glosarium", [(x.get("id"), x.get("term")) for x in m])
            return gdb.build_context_text(m)
        finally:
            c.close()
    return _safe(run)


def _disambig_block(query, sem=None):
    # CATATAN: sengaja TIDAK memakai tanggal interaksi. Pemilihan DJP Online vs
    # Coretax bergantung pada MASA PAJAK yang ditanyakan user (dibaca LLM dari
    # teks pertanyaan), bukan kapan user bertanya. match() tanpa tanggal => tidak
    # ada putusan otomatis; aturannya disajikan agar LLM yang menerapkan.
    def run():
        c = ddb.init_db(ddb.connect())
        try:
            m = _merge(ddb.match(c, query, limit=4), sem, limit=5)
            _log_pustaka("disambiguasi", [(x.get("id"), x.get("pemicu")) for x in m])
            return ddb.build_context_text(m)
        finally:
            c.close()
    return _safe(run)


def _intentmap_block(query, sem=None):
    def run():
        c = imdb.init_db(imdb.connect())
        try:
            m = _merge(imdb.match(c, query, limit=4), sem, limit=5)
            _log_pustaka("intentmap", [(x.get("id"), x.get("intent")) for x in m])
            return imdb.build_context_text(m)
        finally:
            c.close()
    return _safe(run)


def _catalog_desc_block(query, sem=None):
    # Katalog Intent (deskripsi maksud & cakupan; sebagian draf AI). Draf yang
    # belum diverifikasi analis tetap disajikan tapi ditandai jelas.
    def run():
        c = imdb.init_db(imdb.connect())
        try:
            m = _merge(imdb.match_catalog(c, query, limit=4), sem, limit=5)
            _log_pustaka("katalog", [(x.get("id"), x.get("intent")) for x in m])
            return imdb.build_catalog_context_text(m)
        finally:
            c.close()
    return _safe(run)


def build_analysis_context(query, max_chars=1800):
    """Gabungkan konteks relevan dari pustaka (hybrid). Kosong bila tak cocok."""
    q = (query or "").strip()
    if not q:
        return ""
    sem = _semantic_all(q)
    blocks = [b for b in (
        _glossary_block(q, sem.get("glosarium")),
        _disambig_block(q, sem.get("disambiguasi")),
        _intentmap_block(q, sem.get("intentmap")),
        _catalog_desc_block(q, sem.get("katalog")),
    ) if b and b.strip()]
    if not blocks:
        return ""
    body = "\n\n".join(blocks)
    if max_chars and len(body) > max_chars:
        body = body[:max_chars].rstrip() + "\u2026"
    return HEADER + "\n\n" + body + "\n" + FOOTER


def system_suffix(query, max_chars=1800):
    """build_analysis_context dengan dua newline di depan; kosong bila tak ada."""
    ctx = build_analysis_context(query, max_chars=max_chars)
    return ("\n\n" + ctx) if ctx else ""


if __name__ == "__main__":
    # Smoke test manual (butuh DB berisi seed dari halaman terkait).
    for qq in [
        "saya lupa email dan no hp yang lama",
        "lupa password untuk lapor SPT masa pajak Desember 2024",
        "apa itu efin",
        "",
    ]:
        print("=" * 60)
        print("Q:", repr(qq))
        print(build_analysis_context(qq) or "(kosong)")
