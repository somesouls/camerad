# -*- coding: utf-8 -*-
"""rag_grounding_patch.py — Tahap 3: guardrail grounding jawaban RAG.

Guardrail dijalankan sebagai post-processor atas rag_engine.answer():

1. ANTI-LINK TIDAK RESMI (env RAG_GUARD_URL, default aktif)
   Membuang URL pada BODY jawaban yang bukan domain resmi (*.go.id) dan tidak
   muncul di konteks retrieval — mis. pemendek t.co / bit.ly / s.id atau tautan
   sosmed x.com / twitter.com yang kerap 'dikarang' model sebagai link login
   Coretax. Teks anchor dipertahankan; pemendek yang jelas mengarah ke login
   Coretax diganti domain resmi coretaxdjp.pajak.go.id.

2. ANTI-KARANG PASAL (env RAG_GUARD_PASAL, default aktif)
   Bila jawaban memuat rujukan hukum spesifik (PMK/PER/PP/UU/PERPPU/PERDIRJEN/
   PBB + nomor) yang TIDAK terdukung konteks retrieval, jawaban dipaksa abstain
   (fallback) agar tidak menyebarkan dasar hukum fiktif pada domain sensitif.

3. ANTI-KARANG PASAL TELANJANG (env RAG_GUARD_PASAL_BARE, default NONAKTIF)
   Menangkap rujukan "Pasal N" (tanpa jenis peraturan) yang tak muncul di
   konteks retrieval. Karena istilah seperti "PPh Pasal 21/23/25" lazim dipakai
   sehari-hari, guardrail ini bisa agresif; maka default NONAKTIF dengan dua
   mode via RAG_GUARD_PASAL_BARE_MODE: "flag" (default; hanya menandai di
   res['guardrail'], jawaban TIDAK diubah) atau "abstain" (paksa fallback).

Semua guardrail FAIL-OPEN: bila terjadi galat, jawaban asli dikembalikan apa
adanya. Mengikuti pola monkey-patch rag_successor_patch / rag_calibration_patch
dan membungkus rag_engine.answer + rag_engine._render_prompt (untuk menangkap
konteks yang benar-benar dipakai), sehingga berlaku untuk SEMUA pemanggil:
chat produksi, webhook Dialogflow, playground /rag-lab, dan harness /rag-eval.
"""
import os
import re
import threading

import rag_engine as _re
import rag_config_db as _rcfg

_TLS = threading.local()
_SENTINEL = object()


def _flag(name, default):
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() not in ("0", "false", "no", "off")


# ---- Guardrail 1: URL tidak resmi ---------------------------------------
_URL_RE = re.compile(r"https?://[^\s)\]>\"'}]+", re.I)
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\((https?://[^\s)]+)\)", re.I)
_OFFICIAL_HOST_RE = re.compile(r"(?:^|\.)go\.id$", re.I)
_SHORTENERS = ("t.co", "bit.ly", "s.id", "tinyurl.com", "goo.gl", "ow.ly",
               "fb.me", "lnkd.in", "cutt.ly", "shorturl.at")
_CORETAX_OFFICIAL = "https://coretaxdjp.pajak.go.id"


def _host(url):
    m = re.match(r"https?://([^/\s]+)", url or "", re.I)
    return (m.group(1).split(":")[0].lower() if m else "")


def _url_allowed(url, ctx_lower):
    host = _host(url)
    if not host:
        return True
    if _OFFICIAL_HOST_RE.search(host):
        return True
    if url.lower() in ctx_lower:        # persis muncul sbg sumber di konteks
        return True
    return False


def _looks_url(s):
    return bool(re.match(r"\s*https?://", s or "", re.I))


def _repl_for(url):
    """Pengganti utk URL yang ditolak: link resmi Coretax utk pemendek, selain
    itu string kosong (dibuang)."""
    return _CORETAX_OFFICIAL if _host(url) in _SHORTENERS else ""


def _sanitize_urls(text, ctx_lower):
    if not text:
        return text, 0
    removed = [0]

    def _md(m):
        label, url = m.group(1), m.group(2)
        if _url_allowed(url, ctx_lower):
            return m.group(0)
        removed[0] += 1
        rep = _repl_for(url)
        if rep:
            lab = rep if _looks_url(label) else label
            return "[%s](%s)" % (lab, rep)
        return "" if _looks_url(label) else label

    def _raw(m):
        url = m.group(0)
        if _url_allowed(url, ctx_lower):
            return url
        removed[0] += 1
        return _repl_for(url)

    text = _MD_LINK_RE.sub(_md, text)
    text = _URL_RE.sub(_raw, text)
    text = re.sub(r"\(\s*\)", "", text)            # kurung kosong sisa
    text = re.sub(r"[ \t]{2,}", " ", text)          # spasi ganda
    text = re.sub(r"\s+([.,;])", r"\1", text)       # spasi sebelum tanda baca
    return text, removed[0]


# ---- Guardrail 2: rujukan hukum tak terdukung ---------------------------
_TYPE = r"(PERPPU|PERPU|PERDIRJEN|PMK|PBB|PER|PP|UUD|UU)"
_REG_RE = re.compile(
    r"\b" + _TYPE + r"\s*(?:no(?:mor)?\.?)?\s*[-/]?\s*(\d{1,4})\b", re.I)


def _reg_pairs(text):
    """Kumpulan (JENIS, NOMOR) rujukan regulasi. Nomor berawalan nol (kode
    klasifikasi mis. PMK.010) diabaikan agar tak salah tandai."""
    out = set()
    for m in re.finditer(_REG_RE, text or ""):
        typ = m.group(1).upper()
        num = m.group(2)
        if num.startswith("0"):
            continue
        if typ == "PERPU":
            typ = "PERPPU"
        out.add((typ, num))
    return out


def _ungrounded_regs(answer_text, ctx):
    ans = _reg_pairs(answer_text)
    if not ans:
        return set()
    ctxp = _reg_pairs(ctx or "")
    return {p for p in ans if p not in ctxp}


# ---- Guardrail 3: pasal 'telanjang' tak terdukung -----------------------
_PASAL_RE = re.compile(r"\bpasal\s+(\d{1,4}[A-Za-z]?)\b", re.I)


def _pasal_nums(text):
    out = set()
    for m in re.finditer(_PASAL_RE, text or ""):
        out.add(m.group(1).upper())
    return out


def _ungrounded_pasal(answer_text, ctx):
    ans = _pasal_nums(answer_text)
    if not ans:
        return set()
    ctxp = _pasal_nums(ctx or "")
    return {p for p in ans if p not in ctxp}


# ---- Pembungkus answer ---------------------------------------------------
def _install():
    if getattr(_re, "_grounding_patched", False):
        return
    _orig_answer = _re.answer
    _orig_render = _re._render_prompt

    def _render_capture(tmpl, context, sumber_txt, fallback):
        try:
            _TLS.ctx = context or ""
        except Exception:
            pass
        return _orig_render(tmpl, context, sumber_txt, fallback)

    def _guarded_answer(question, profile, override=None, history=None,
                        diagnostics=False):
        try:
            _TLS.ctx = _SENTINEL
        except Exception:
            pass
        res = _orig_answer(question, profile, override=override,
                           history=history, diagnostics=diagnostics)
        try:
            if not isinstance(res, dict) or not res.get("ok"):
                return res
            if not res.get("grounded"):
                return res
            ans = res.get("answer") or ""
            if not ans.strip():
                return res
            ctx = getattr(_TLS, "ctx", _SENTINEL)
            ctx = "" if ctx is _SENTINEL else (ctx or "")
            ctx_lower = ctx.lower()
            notes = []
            bare_info = None

            if _flag("RAG_GUARD_URL", True):
                new_ans, removed = _sanitize_urls(ans, ctx_lower)
                if removed:
                    ans = new_ans
                    notes.append("buang/normalisasi %d tautan tidak resmi" % removed)

            if _flag("RAG_GUARD_PASAL", True):
                bad = _ungrounded_regs(ans, ctx)
                if bad:
                    fb = profile.get("fallback") or _rcfg.FALLBACK_DEFAULT
                    res["answer"] = fb
                    res["grounded"] = False
                    res["sources"] = []
                    info = {"abstain": True,
                            "alasan": "rujukan hukum tak terdukung konteks",
                            "regulasi": sorted("%s %s" % (t, n) for t, n in bad)}
                    res["guardrail"] = info
                    if diagnostics and isinstance(res.get("diagnostics"), dict):
                        res["diagnostics"].setdefault("guardrail", []).append(info)
                    return res

            if _flag("RAG_GUARD_PASAL_BARE", False):
                bad_pasal = _ungrounded_pasal(ans, ctx)
                if bad_pasal:
                    mode = (os.environ.get("RAG_GUARD_PASAL_BARE_MODE") or "flag").strip().lower()
                    detail = sorted("Pasal %s" % p for p in bad_pasal)
                    if mode == "abstain":
                        fb = profile.get("fallback") or _rcfg.FALLBACK_DEFAULT
                        res["answer"] = fb
                        res["grounded"] = False
                        res["sources"] = []
                        info = {"abstain": True,
                                "alasan": "rujukan pasal tak terdukung konteks",
                                "pasal": detail}
                        res["guardrail"] = info
                        if diagnostics and isinstance(res.get("diagnostics"), dict):
                            res["diagnostics"].setdefault("guardrail", []).append(info)
                        return res
                    bare_info = detail
                    notes.append("tandai %d rujukan pasal tak terdukung konteks" % len(detail))

            if notes:
                res["answer"] = ans
                g = {"abstain": False, "catatan": notes}
                if bare_info:
                    g["pasal_takterdukung"] = bare_info
                res["guardrail"] = g
        except Exception as e:        # fail-open
            try:
                res["guardrail_error"] = str(e)[:160]
            except Exception:
                pass
        return res

    _re.answer = _guarded_answer
    _re._render_prompt = _render_capture
    _re._grounding_patched = True
    print("[rag_grounding_patch] guardrail grounding aktif (url=%s, pasal=%s, pasal_bare=%s)."
          % (_flag("RAG_GUARD_URL", True), _flag("RAG_GUARD_PASAL", True),
             _flag("RAG_GUARD_PASAL_BARE", False)),
          flush=True)


_install()


# --- Registrasi HyDE (opsional, env RAG_HYDE; default nonaktif) ----------
# Diimpor di sini agar ikut ter-load saat web_app mengimpor rag_grounding_patch,
# tanpa perlu menyentuh web_app.py. Fail-open bila modul tak ada.
try:
    import rag_hyde_patch  # noqa: F401
except Exception as _e:
    print("[rag_grounding_patch] rag_hyde_patch tidak dimuat: %s" % _e, flush=True)
