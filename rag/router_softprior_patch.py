# -*- coding: utf-8 -*-
"""rag_router_softprior_patch.py — Router v2 \"soft-prior\" (jangan hard-cut sumber).

Router v2 (rag.router) mempercepat kueri yang JELAS peraturan dengan MELEWATI
sumber lambat/kurang relevan (default: intent, awe) — sumber itu dikembalikan
pada kunci 'tunda' dan TIDAK diretrieval sama sekali (hard-cut). Efek samping:
untuk kueri campuran yang tampak \"peraturan\" (mis. teknis + dasar hukum),
konteks lintas-kategori yang relevan (jawaban intent / percakapan AWE) bisa
HILANG total.

Patch ini mengubah hard-cut menjadi SOFT-PRIOR: sumber yang akan dilewati Router
v2 TIDAK dibuang, melainkan DIDEMOSIKAN ke prioritas TERENDAH (ditempel di akhir
`ordered`). Dengan begitu sumber utama (peraturan/sop) tetap didahulukan — mutu
& sebagian besar manfaat latensi terjaga via budget konteks (MAKS_KONTEKS) +
rerank — tetapi sumber lintas-kategori tetap tersedia sebagai jaring pengaman
recall.

Sifat:
  * Env RAG_ROUTER_SOFTPRIOR=1 (default aktif). Set 0 -> kembali ke perilaku
    hard-cut Router v2 (reversibel penuh).
  * Hanya bekerja bila Router v2 menghasilkan 'tunda' (kueri peraturan-jelas);
    kueri lain tidak terpengaruh (nol dampak).
  * TIDAK PERNAH menambah sumber di luar yang sudah diurutkan router (berasal
    dari `allowed` / checkbox Konfigurasi). Hanya mengubah URUTAN.
  * Sumber yang didemosikan dicatat pada kunci 'tunda_soft' untuk transparansi
    diagnostik; 'tunda' dikosongkan.
  * Fail-open: kegagalan apa pun -> kembalikan hasil route asli tanpa perubahan.

Idempoten: menandai rag.router._softprior_patched agar tak dobel-bungkus.
"""
import os

try:
    import rag.router as _r
except Exception:            # pragma: no cover
    _r = None


def _enabled():
    return str(os.environ.get("RAG_ROUTER_SOFTPRIOR", "1")).strip().lower() not in (
        "0", "false", "no", "off")


def _apply():
    if _r is None or getattr(_r, "_softprior_patched", False):
        return
    _orig_route = _r.route

    def route(q, allowed=None):
        res = _orig_route(q, allowed)
        try:
            if not _enabled() or not isinstance(res, dict):
                return res
            tunda = list(res.get("tunda") or [])
            if not tunda:
                return res
            ordered = list(res.get("ordered") or [])
            # Demosikan (bukan buang): tempel sumber 'tunda' di akhir prioritas.
            for s in tunda:
                if s not in ordered:
                    ordered.append(s)
            res["ordered"] = ordered
            res["tunda_soft"] = tunda          # transparansi diagnostik
            res["tunda"] = []
            res["metode"] = (res.get("metode") or "") + "+softprior"
        except Exception:
            return res
        return res

    _r.route = route
    _r._softprior_patched = True


_apply()
