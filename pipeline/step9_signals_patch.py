# -*- coding: utf-8 -*-
"""Bungkus pipeline.routes.step9_load (SETELAH step9_patch): tambah 'sinyal'
analisis per baris MKTA (acuan analis). Tanpa mengubah step9_patch.py.
Fail-open. Diaktifkan via chain-import di titik aktivasi (step10_patch)."""
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

_orig_step9_load = pr.step9_load


def step9_load(cfg, ctx):
    res = _orig_step9_load(cfg, ctx)
    try:
        rows = res.get("rows") if isinstance(res, dict) else None
        if not rows:
            return res
        conn = asig.open_conn(gdb, ddb)
        try:
            for r in rows:
                try:
                    q = r.get("pertanyaan") or r.get("user") or ""
                    llm = r.get("llm") or r.get("seharusnya") or ""
                    sig = asig.hitung_sinyal(q, options=None,
                                             intent_mesin=r.get("intent"),
                                             intent_llm=llm,
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


pr.step9_load = step9_load
print("[step9_signals_patch] step9_load dibungkus (sinyal analisis MKTA aktif).",
      flush=True)
