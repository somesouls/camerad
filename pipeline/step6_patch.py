# -*- coding: utf-8 -*-
"""Bungkus pipeline.routes.step6_load: tambah 'sinyal' analisis per baris
fallback (acuan analis) tanpa menyentuh pipeline/steps.py. Fail-open.
Diaktifkan via chain-import di titik aktivasi (step10_patch)."""
import pipeline.routes as pr
import pipeline.analysis_signals as asig

try:
    import knowledge.glossary_db as gdb
except Exception:
    gdb = None
try:
    import knowledge.disambig_db as ddb
except Exception:
    ddb = None

_orig_step6_load = pr.step6_load


def step6_load(cfg, ctx):
    res = _orig_step6_load(cfg, ctx)
    try:
        rows = res.get("rows") if isinstance(res, dict) else None
        if not rows:
            return res
        conn = asig.open_conn(gdb, ddb)
        try:
            for r in rows:
                try:
                    q = r.get("pertanyaan") or r.get("user") or r.get("query") or ""
                    sig = asig.hitung_sinyal(q, options=r.get("options") or [],
                                             conn=conn, gdb=gdb, ddb=ddb)
                    r["sinyal"] = sig
                    r["badges"] = sig.get("badges", [])
                except Exception:
                    r["sinyal"] = {}
                    r["badges"] = []
        finally:
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass
    except Exception:
        pass
    return res


pr.step6_load = step6_load
print("[step6_patch] step6_load dibungkus (sinyal analisis fallback aktif).",
      flush=True)
