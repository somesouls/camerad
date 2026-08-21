# -*- coding: utf-8 -*-
"""intents_patch.py — Fase 2 (C).

Menambah aksi 'intents' pada dispatch pipeline: mengembalikan DAFTAR NAMA INTENT
Dialogflow yang sudah ditarik (katalog intent hasil sinkron Step 3/4) untuk
kolom pencarian intent di Step 6 (Intent Judgement LLM) & Step 9 (Intent
Seharusnya). Read-only. Fail-open: bila katalog kosong/gagal -> {ok:True, intents:[]}.

Sumber: knowledge.intentmap_db
  1) intentmap_catalog (disinkron dari intent Dialogflow yg ditarik) via catalog_list()
  2) fallback: tabel kebijakan intentmap via list_intents()

Pemasangan (web_app.py): import SETELAH pipeline.routes diimpor, mis.
    import pipeline.routes
    import pipeline.intents_patch   # noqa: F401
dispatch() dipanggil via nama global di modul routes, sehingga reassignment
pr.dispatch otomatis dipakai (late-binding), tanpa menyentuh routes.py.
"""
import pipeline.routes as pr

try:
    import knowledge.intentmap_db as imdb
except Exception:
    imdb = None

_orig_dispatch = pr.dispatch


def _intent_names(q="", lang=""):
    """Kembalikan daftar nama intent unik (urut alfabetis, case-insensitive)."""
    names = []
    seen = set()
    if imdb is None:
        return names
    try:
        conn = imdb.connect()
    except Exception:
        return names
    try:
        lg = (lang or "").strip().lower() or None
        qq = (q or "").strip() or None
        rows = []
        try:
            rows = imdb.catalog_list(conn, q=qq, filt="all", limit=5000, lang=lg)
        except Exception:
            rows = []
        for r in rows:
            nm = ((r.get("intent") if isinstance(r, dict) else "") or "").strip()
            if nm and nm.lower() not in seen:
                seen.add(nm.lower())
                names.append(nm)
        # Fallback: tabel kebijakan intentmap bila katalog masih kosong.
        if not names:
            try:
                pol = imdb.list_intents(conn, q=qq, limit=5000, lang=lg)
            except Exception:
                pol = []
            for r in pol:
                nm = ((r.get("intent") if isinstance(r, dict) else "") or "").strip()
                if nm and nm.lower() not in seen:
                    seen.add(nm.lower())
                    names.append(nm)
    finally:
        try:
            conn.close()
        except Exception:
            pass
    names.sort(key=lambda s: s.lower())
    return names


def dispatch(action, cfg, ctx):
    if action == "intents":
        q = (ctx.R("q", "") or "").strip()
        lang = (ctx.R("lang", "") or "").strip()
        items = _intent_names(q, lang)
        return {"ok": True, "intents": items, "total": len(items), "term": q}
    return _orig_dispatch(action, cfg, ctx)


pr.dispatch = dispatch

try:
    print("[intents_patch] aktif: aksi 'intents' (katalog intent untuk pencarian Step 6/9)", flush=True)
except Exception:
    pass
