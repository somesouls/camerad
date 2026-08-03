# -*- coding: utf-8 -*-
"""
knowledge_ctx.py
----------------
Helper TERPUSAT untuk menyambungkan tiga pustaka pengetahuan analis ke mesin
analisis & chat:

  1. Glosarium Istilah Pajak      (glossary_db)   -> arti istilah (EFIN, Coretax, ...)
  2. Pustaka Disambiguasi         (disambig_db)   -> makna yang benar utk frasa ambigu
  3. Peta Intent & Maksud Analis  (intentmap_db)  -> kunci jawaban / kebijakan analis

Fungsi utama:
    build_analysis_context(query, max_chars=1800) -> str
        Blok teks ringkas berisi HANYA entri yang relevan dengan `query`
        (hasil match() tiap pustaka), siap disuntik ke system/instruction prompt.
        String kosong bila tak ada yang relevan (aman sebagai no-op).

        Disambiguasi DJP Online vs Coretax TIDAK memakai tanggal interaksi;
        aturannya (berbasis MASA PAJAK yang ditanyakan user) hanya disajikan ke
        LLM untuk diterapkan sendiri dari teks pertanyaan.

    system_suffix(query, max_chars=1800) -> str
        Sama, tetapi diawali dua newline agar praktis ditempel ke akhir
        system prompt. Kosong bila tak ada konteks.

Hanya memakai stdlib + modul db lokal. Semua kegagalan ditangani diam-diam
supaya analisis tetap berjalan walau pustaka kosong / bermasalah.
"""
import glossary_db as gdb
import disambig_db as ddb
import intentmap_db as imdb
import pustaka_stats as pstats  # statistik pemakaian pustaka

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


def _glossary_block(query):
    def run():
        c = gdb.init_db(gdb.connect())
        try:
            m = gdb.match(c, query, limit=4)
            _log_pustaka("glosarium", [(x.get("id"), x.get("term")) for x in m])
            return gdb.build_context_text(m)
        finally:
            c.close()
    return _safe(run)


def _disambig_block(query):
    # CATATAN: sengaja TIDAK memakai tanggal interaksi. Pemilihan DJP Online vs
    # Coretax bergantung pada MASA PAJAK yang ditanyakan user (dibaca LLM dari
    # teks pertanyaan), bukan kapan user bertanya. match() tanpa tanggal => tidak
    # ada putusan otomatis; aturannya disajikan agar LLM yang menerapkan.
    def run():
        c = ddb.init_db(ddb.connect())
        try:
            m = ddb.match(c, query, limit=4)
            _log_pustaka("disambiguasi", [(x.get("id"), x.get("pemicu")) for x in m])
            return ddb.build_context_text(m)
        finally:
            c.close()
    return _safe(run)


def _intentmap_block(query):
    def run():
        c = imdb.init_db(imdb.connect())
        try:
            m = imdb.match(c, query, limit=4)
            _log_pustaka("intentmap", [(x.get("id"), x.get("intent")) for x in m])
            return imdb.build_context_text(m)
        finally:
            c.close()
    return _safe(run)


def _catalog_desc_block(query):
    # Katalog Intent (deskripsi maksud & cakupan; sebagian draf AI). Draf yang
    # belum diverifikasi analis tetap disajikan tapi ditandai jelas.
    def run():
        c = imdb.init_db(imdb.connect())
        try:
            m = imdb.match_catalog(c, query, limit=4)
            _log_pustaka("katalog", [(x.get("id"), x.get("intent")) for x in m])
            return imdb.build_catalog_context_text(m)
        finally:
            c.close()
    return _safe(run)


def build_analysis_context(query, max_chars=1800):
    """Gabungkan konteks relevan dari 3 pustaka. Kosong bila tak ada yang cocok."""
    q = (query or "").strip()
    if not q:
        return ""
    blocks = [b for b in (
        _glossary_block(q),
        _disambig_block(q),
        _intentmap_block(q),
        _catalog_desc_block(q),
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
