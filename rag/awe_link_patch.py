# -*- coding: utf-8 -*-
"""rag/awe_link_patch.py — PR B: jadikan rujukan livechat AWE bisa diklik.

Masalah: sumber "Percakapan AWE" yang dikembalikan mesin RAG TIDAK punya field
`url`, sehingga di halaman Chat Baru kartu sumbernya hanya "pratinjau" (tak bisa
diklik). Padahal transkrip percakapan sudah tersimpan
(avaya.db: awe_conversations.transkrip_json) dan bisa ditampilkan sebagai
gelembung percakapan lengkap.

Patch ini (mengikuti konvensi *_patch.py di repo):
  1. Membungkus dispatcher AWE terakhir (_ctx_awe / _DISPATCH["awe"], yang pada
     produksi = rag.sources_patch._ctx_awe_v2) dan MENAMBAHKAN `url` ke tiap
     sumber "Percakapan AWE": /api/rag/agent/transkrip/<sid>. SID diambil dari
     field `ref` ("SID <sid>"). Konteks yang dikirim ke LLM TIDAK diubah.
  2. Mendaftarkan rute transkrip (awe.transcript_routes) yang merender transkrip
     sebagai gelembung, PII dimask, dan hanya untuk pengguna login (area 'chat'
     → peran 'agent' pun boleh).

Gagal-anggun: bila apa pun gagal, perilaku kembali seperti semula.
Env RAG_AWE_LINK=0 mematikan patch ini.

Dipasang lewat web_app.py SETELAH rag.sources_patch agar membungkus versi
terakhir sumber AWE.
"""
import os
import functools

import rag.engine as _re


def _on():
    return str(os.environ.get("RAG_AWE_LINK", "1")).strip().lower() not in (
        "0", "false", "no", "off")


def _sid_from_ref(ref):
    s = str(ref or "").strip()
    # format dari rag.sources_patch: "SID <sid>"
    if s[:3].upper() == "SID":
        s = s[3:].strip()
    return s


def _add_awe_urls(sources):
    if not isinstance(sources, list):
        return sources
    for s in sources:
        if not isinstance(s, dict) or s.get("url"):
            continue
        if str(s.get("sumber") or "").strip().lower() != "percakapan awe":
            continue
        sid = _sid_from_ref(s.get("ref"))
        if sid:
            s["url"] = "/api/rag/agent/transkrip/" + sid
    return sources


def _wrap(fn):
    if fn is None or getattr(fn, "_awe_link_wrapped", False):
        return fn

    @functools.wraps(fn)
    def _inner(*args, **kwargs):
        res = fn(*args, **kwargs)
        try:
            ctx, sources = res
            sources = _add_awe_urls(sources)
            return ctx, sources
        except Exception:
            return res

    _inner._awe_link_wrapped = True
    return _inner


def _install():
    if not _on():
        print("[rag_awe_link_patch] dimatikan (RAG_AWE_LINK=0).", flush=True)
        return
    if getattr(_re, "_awe_link_patched", False):
        return
    try:
        wrapped = _wrap(getattr(_re, "_ctx_awe", None))
        if wrapped is not None:
            _re._ctx_awe = wrapped
            if isinstance(getattr(_re, "_DISPATCH", None), dict):
                _re._DISPATCH["awe"] = wrapped
        _re._awe_link_patched = True
        print("[rag_awe_link_patch] URL transkrip AWE aktif "
              "(/api/rag/agent/transkrip/<sid>).", flush=True)
    except Exception as e:
        print("[rag_awe_link_patch] patch AWE-URL gagal:", e, flush=True)


# Daftarkan rute transkrip. Fail-soft: bila gagal, patch URL di atas tetap jalan
# (tautan akan 404) tanpa mematikan alur chat.
try:
    import awe.transcript_routes as _awe_tx_routes
    _awe_tx_routes.register()
except Exception as _awe_tx_exc:
    print("[rag_awe_link_patch] rute transkrip dilewati:", _awe_tx_exc, flush=True)

_install()
