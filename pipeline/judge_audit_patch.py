# -*- coding: utf-8 -*-
"""judge_audit_patch.py — Audit "acuan analis" (Opsi A).

Menandai tiap baris review Step 6 (Analisis Fallback) & Step 9 (Analisis MKTA)
apakah judgement LLM PUNYA acuan pengetahuan analis, beserta SUMBER mana yang
cocok: glosarium / peta intent / disambiguasi / katalog.

Meniru pencocokan knowledge.ctx.build_analysis_context (keyword + semantik SBERT
bila tersedia) TANPA menulis ulang berkas .xlsx dan TANPA mencatat statistik
pemakaian pustaka (memakai matcher mentah, bukan blok ber-log). Dihitung saat
LOAD (step6_load/step9_load), sama pola dgn sinyal Fase 1. Fail-open total.

Aktivasi: chain-import SETELAH step9_signals_patch di titik aktivasi.
Env: JUDGE_AUDIT_NO_SEMANTIC=1 -> lewati semantik (lebih cepat, keyword saja).
"""
import os
import pipeline.routes as pr

try:
    import knowledge.glossary_db as gdb
except Exception:
    gdb = None
try:
    import knowledge.disambig_db as ddb
except Exception:
    ddb = None
try:
    import knowledge.intentmap_db as imdb
except Exception:
    imdb = None
try:
    from knowledge.ctx import _merge as _ctx_merge, _semantic_all as _ctx_sem
except Exception:
    _ctx_merge = None
    _ctx_sem = None

_LABELS = {"glosarium": "glosarium", "disambiguasi": "disambiguasi",
           "intentmap": "peta intent", "katalog": "katalog"}


def _merge(primary, extra, limit=5):
    if _ctx_merge is not None:
        try:
            return _ctx_merge(primary, extra, limit=limit)
        except Exception:
            pass
    return list(primary or [])[:limit]


def _sem(q):
    if _ctx_sem is None or os.environ.get("JUDGE_AUDIT_NO_SEMANTIC") == "1":
        return {}
    try:
        return _ctx_sem(q) or {}
    except Exception:
        return {}


def _hit(mod, attr, q, sem_list):
    if mod is None:
        return False
    try:
        c = mod.init_db(mod.connect())
        try:
            base = getattr(mod, attr)(c, q, limit=4) or []
        finally:
            try:
                c.close()
            except Exception:
                pass
        return bool(_merge(base, sem_list, limit=5))
    except Exception:
        return False


def audit_acuan(query):
    """{available, glosarium, disambiguasi, intentmap, katalog, sources[]}."""
    res = {"available": False, "glosarium": False, "disambiguasi": False,
           "intentmap": False, "katalog": False, "sources": []}
    q = (query or "").strip()
    if not q:
        return res
    sem = _sem(q)
    checks = (
        ("glosarium", gdb, "match", "glosarium"),
        ("disambiguasi", ddb, "match", "disambiguasi"),
        ("intentmap", imdb, "match", "intentmap"),
        ("katalog", imdb, "match_catalog", "katalog"),
    )
    for key, mod, attr, sem_key in checks:
        if _hit(mod, attr, q, sem.get(sem_key)):
            res[key] = True
            res["sources"].append(_LABELS[key])
    res["available"] = bool(res["sources"])
    return res


def _augment(rows, tag):
    total = avail = 0
    for r in rows:
        try:
            q = r.get("pertanyaan") or r.get("user") or r.get("query") or ""
            a = audit_acuan(q)
            r["acuan"] = a
            total += 1
            if a.get("available"):
                avail += 1
        except Exception:
            r["acuan"] = {}
    print("[judge_audit] " + tag + ": acuan analis tersedia di "
          + str(avail) + "/" + str(total) + " baris.", flush=True)


def _wrap(name, tag):
    orig = getattr(pr, name, None)
    if not callable(orig):
        print("[judge_audit] " + name + " tidak ada; audit dilewati.", flush=True)
        return
    def wrapped(cfg, ctx):
        res = orig(cfg, ctx)
        try:
            rows = res.get("rows") if isinstance(res, dict) else None
            if rows:
                _augment(rows, tag)
        except Exception:
            pass
        return res
    setattr(pr, name, wrapped)


_wrap("step6_load", "Step6")
_wrap("step9_load", "Step9")
print("[judge_audit] step6_load & step9_load dibungkus (audit acuan analis aktif).",
      flush=True)
