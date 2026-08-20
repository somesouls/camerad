# -*- coding: utf-8 -*-
"""handoff_routing_patch.py — Terapkan logika PERUTEAN LAYANAN (handoff) ke RAG.

Membungkus rag_engine.answer: bila pertanyaan pengguna cocok dengan salah satu
intent LAYANAN pada tabel handoff_routing (handoff_routing_db), tambahkan blok
"PANDUAN PERUTEAN LAYANAN" ke system_prompt profil (SALINAN, tidak mengubah DB)
sebelum sintesis. Dengan begitu:
  - Pertanyaan informasi murni (tak cocok tabel) => perilaku RAG normal.
  - Intent layanan (Lupa EFIN, Perubahan Data, Aktivasi EFIN, Konfirmasi NPWP,
    dst.) => LLM mengarahkan ke kanal mandiri/agent/KPP sesuai aturan.

Non-invasif & gagal-anggun: bila modul/tabel bermasalah, answer asli dipakai apa
adanya. WAJIB diimpor SETELAH rag_grounding_patch agar membungkus versi answer
terakhir (grounding tetap berjalan di dalam).
"""
import rag.engine as _re

try:
    from handoff import routing_db as _hrdb
except Exception:            # pragma: no cover
    _hrdb = None


def _augment_profile(profile, question):
    """Kembalikan SALINAN profile dgn system_prompt + panduan perutean, atau
    profile asli bila tak ada intent layanan yang cocok."""
    if _hrdb is None or not isinstance(profile, dict):
        return profile
    try:
        row = _hrdb.match_routing(question)
    except Exception:
        row = None
    if not row:
        return profile
    try:
        guide = _hrdb.guidance_text(row)
    except Exception:
        guide = ""
    if not guide:
        return profile
    base = profile.get("system_prompt") or ""
    p2 = dict(profile)
    p2["system_prompt"] = (base + "\n\n" + guide) if base else guide
    return p2


def _install():
    if getattr(_re, "_handoff_routing_patched", False):
        return
    _orig_answer = _re.answer

    def answer(question, profile, *args, **kwargs):
        try:
            profile = _augment_profile(profile, question)
        except Exception:
            pass
        return _orig_answer(question, profile, *args, **kwargs)

    _re.answer = answer
    _re._handoff_routing_patched = True
    try:
        print("[handoff_routing_patch] perutean layanan aktif (mandiri/agent/KPP).",
              flush=True)
    except Exception:
        pass


_install()
