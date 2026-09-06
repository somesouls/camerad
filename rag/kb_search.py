# -*- coding: utf-8 -*-
"""kb_search.py — API retrieval-only basis pengetahuan RAG untuk konsumen non-chat.

Membungkus mesin retrieval rag.engine (TANPA LLM sintesis) agar jalur lain —
khususnya loop agentic READ-ONLY di knowledge/agentic.py — dapat menelusuri
sumber TEKSTUAL (peraturan, SOP, media sosial, percakapan AWE, intent) selain
database terstruktur di db.registry.

Sifat: ADITIF & NON-BREAKING. Tidak mengubah rag.engine; hanya memakai ulang
perakit konteks (_assemble) dan dedup sumber (_dedup_sources) yang sudah ada
di engine (yang di dalamnya memanggil retrieval per-sumber _retrieve_one dan
sudah gagal-anggun per sumber). Read-only.

Catatan: retrieval memakai default gerbang cosine/knob (env>default) karena
tidak menyetel profil aktif (thread-local rag.calibration). Ini disengaja —
jalur agentic bukan chat per-profil; INERT bila knob belum diset.
"""
import rag.engine as engine
import rag.config_db as rcfg


def sumber_valid():
    """Daftar key sumber yang valid (intent, awe, sosmed, peraturan, sop)."""
    return list(rcfg.SUMBER_VALID)


def retrieve_context(question, sources=None, max_chars=None):
    """Rakit konteks tekstual dari basis pengetahuan TANPA memanggil LLM.

    - question : pertanyaan / kata kunci pengguna.
    - sources  : subset dari sumber_valid(); None/kosong/invalid = semua sumber.
    - max_chars: batas panjang konteks (opsional; default batas internal engine).

    Return dict siap-JSON:
      {ok, context, sources:[{sumber,judul,ref,url?}], used:[key,...]}
    Gagal-anggun: exception per-sumber sudah ditelan di engine._retrieve_one;
    query kosong -> ok False.
    """
    q = (question or "").strip()
    if not q:
        return {"ok": False, "error": "query kosong.",
                "context": "", "sources": [], "used": []}
    valid = list(rcfg.SUMBER_VALID)
    if sources:
        keys = [s for s in sources if s in valid]
        if not keys:
            keys = valid
    else:
        keys = valid
    cache = {}
    try:
        context, srcs = engine._assemble(keys, cache, q)
    except Exception as e:
        return {"ok": False, "error": str(e),
                "context": "", "sources": [], "used": []}
    if isinstance(max_chars, int) and max_chars > 0 and len(context) > max_chars:
        context = context[:max_chars].rstrip() + "\u2026"
    used = [k for k in keys if (cache.get(k, ("", []))[0] or "").strip()]
    try:
        srcs = engine._dedup_sources(srcs)
    except Exception:
        pass
    return {"ok": True, "context": context, "sources": srcs, "used": used}


if __name__ == "__main__":
    # Smoke offline-safe: validasi daftar sumber & penanganan query kosong.
    sv = sumber_valid()
    assert "peraturan" in sv and "sop" in sv, sv
    r = retrieve_context("")
    assert r.get("ok") is False, r
    print("KB_SEARCH_SMOKE_OK")
