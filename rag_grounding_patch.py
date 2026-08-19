# -*- coding: utf-8 -*-
"""
rag_grounding_patch.py
----------------------
Tahap 3 (keandalan): guardrail grounding jawaban RAG.

Dua masalah halusinasi yang dijaga patch ini (khususnya untuk sumber PERATURAN):
  1. LLM mengarang/merujuk pasal yang TIDAK ada di konteks retrieval -> jawaban
     hukum jadi berbahaya. Jika jawaban memuat rujukan ber-nomor
     (PMK/PER/PP/UU/KMK/KEP/SE + nomor) yang tidak terdukung konteks, patch
     MEMAKSA abstain dengan kalimat fallback profil.
  2. LLM menempelkan tautan tidak resmi / pemendek (t.co, x.com, bit.ly, dst.)
     di badan jawaban. Patch MEMBUANG/MENORMALKAN tautan tersebut (daftar
     "Sumber Rujukan" tetap berasal dari metadata retrieval yang asli).

Ditambah guardrail v18 (abstain tanpa sumber): bila jawaban akhir PERSIS
kalimat fallback profil (mesin memutuskan abstain), daftar sumber DIKOSONGKAN —
hasil retrieval yang lemah tetap ada saat abstain, dan menampilkannya sebagai
"Sumber Rujukan" menyesatkan petugas seolah sumber itu mendukung jawaban.

Perbaikan v22 (PENTING): signature pembungkus _guarded_answer disamakan PERSIS
dengan rag_engine.answer asli — (question, profile, override=None,
history=None, diagnostics=False, honor_mode=False). Penulisan v18 memakai
signature lama yang tidak menerima kwarg override/diagnostics/honor_mode,
sehingga SEMUA pemanggil (jawab_chat, jawab_lab, eval harness) melempar
TypeError. Kini seluruh kwarg diteruskan apa adanya ke fungsi asli.

Ketentuan seragam yang dicantumkan TANPA nomor (mis. "Ketentuan Umum dan Tata
Cara Perpajakan") TETAP BOLEH — tidak bisa dipalsukan nomornya.

Gagal-anggun: error apa pun -> jawaban asli dilewatkan (fail-open).
Dipasang lewat web_app.py (import rag_grounding_patch), yang membungkus
rag_engine.answer milik profil mana pun (chatbot & agent).

Env:
  RAG_GUARD_URL=0            -> matikan guardrail tautan.
  RAG_GUARD_PASAL=0          -> matikan guardrail anti-karang-pasal.
  RAG_GUARD_FALLBACK_SRC=0   -> matikan penyembunyian sumber saat abstain (v18).
  RAG_GUARD_URL_DOMAINS=...  -> host pemendek/tak-resmi tambahan (koma).
"""
import os
import re

import rag_engine as _re
import rag_config_db as _rcfg

_ORIG_ANSWER = _re.answer


# ---------------------------------------------------------------- env helpers
def _flag(name, default=True):
    v = str(os.environ.get(name, "1" if default else "0")).strip().lower()
    return v not in ("0", "false", "no", "off")


def _extra_domains():
    raw = os.environ.get("RAG_GUARD_URL_DOMAINS", "")
    return [x.strip().lower() for x in raw.split(",") if x.strip()]


# ------------------------------------------------------------- guardrail URL
_SHORTENER = {
    "t.co", "x.com", "twitter.com", "bit.ly", "tinyurl.com", "s.id",
    "shorturl.at", "rebrand.ly", "cutt.ly", "goo.gl", "ow.ly", "buff.ly",
    "youtu.be", "instagram.com", "tiktok.com", "fb.com", "facebook.com",
}
_RE_MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_RE_BARE_URL = re.compile(r"https?://[^\s)>'\"]+", re.I)


def _host_of(url):
    m = re.match(r"https?://([^/\s]+)", url.strip(), re.I)
    return (m.group(1).lower() if m else "")


def _is_unsafe_host(host):
    host = (host or "").lower()
    if not host:
        return False
    bad = _SHORTENER | set(_extra_domains())
    for b in bad:
        if host == b or host.endswith("." + b):
            return True
    return False


def _strip_unsafe_urls(text):
    """Buang markdown-link & bare URL ber-host tak resmi dari badan jawaban.
    Kembalikan (teks_bersih, jumlah_dibuang)."""
    removed = 0

    def _md_repl(m):
        nonlocal removed
        host = _host_of(m.group(2))
        if _is_unsafe_host(host):
            removed += 1
            return m.group(1)  # pertahankan labelnya saja, buang tautannya
        return m.group(0)

    text = _RE_MD_LINK.sub(_md_repl, text)

    def _bare_repl(m):
        nonlocal removed
        url = m.group(0).rstrip(".,;!)]}")
        tail = m.group(0)[len(url):]
        if _is_unsafe_host(_host_of(url)):
            removed += 1
            return ""
        return url + tail

    text = _RE_BARE_URL.sub(_bare_repl, text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip(), removed


# ------------------------------------------------- guardrail rujukan ber-nomor
_RE_REG_REF = re.compile(
    r"\b(PMK|PER|PP|UU|PERPU|KMK|KEP|SE|PERPRES|PERDA)\b"
    r"[^\n]{0,60}?(?:Nomor\s+)?(\d+\s*/[A-Za-z0-9./\-]+/(?:19|20)\d{2})\b",
    re.I,
)


def _ctx_blob(konteks):
    """Teks gabungan seluruh konteks + judul/ref sumber (dipakai sebagai
    bukti dukungan). Huruf kecil semua agar pencocokan toleran."""
    bagian = []
    for k in (konteks or {}).values():
        if isinstance(k, dict):
            bagian.append(str(k.get("teks") or ""))
            for s in (k.get("sumber") or []):
                bagian.append(str(s.get("judul") or ""))
                bagian.append(str(s.get("ref") or ""))
    return " ".join(bagian).lower()


def _norm_nomor(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _refs_in_answer(ans):
    """Daftar token rujukan ber-nomor yang disebut jawaban: {(jenis, nomor_norm)}."""
    out = []
    for m in _RE_REG_REF.finditer(ans or ""):
        jenis = m.group(1).upper()
        if jenis == "UU" and m.group(0).lower().startswith("uur"):
            continue
        out.append((jenis, _norm_nomor(m.group(2))))
    return out


def _ref_supported(jenis, nomor_norm, blob_low, konteks):
    if not nomor_norm:
        return True
    if nomor_norm in blob_low:
        return True
    # Toleransi: nomor persis tercetak di judul/ref sumber (bukan hanya isi).
    for k in (konteks or {}).values():
        if not isinstance(k, dict):
            continue
        for s in (k.get("sumber") or []):
            gab = _norm_nomor(str(s.get("judul") or "") + str(s.get("ref") or ""))
            if nomor_norm in gab:
                return True
    return False


# ----------------------------------------------------------------- pasang patch
def _install():
    if getattr(_re, "_grounding_patched", False):
        return

    # v22: signature disamakan PERSIS dengan rag_engine.answer asli:
    #   answer(question, profile, override=None, history=None,
    #          diagnostics=False, honor_mode=False)
    # Semua kwarg diteruskan apa adanya; fallback TypeError tetap fail-open.
    def _guarded_answer(question, profile, override=None, history=None,
                        diagnostics=False, honor_mode=False):
        try:
            res = _orig_answer(question, profile, override=override,
                               history=history, diagnostics=diagnostics,
                               honor_mode=honor_mode)
        except TypeError:
            res = _orig_answer(question, profile)
        if not isinstance(res, dict):
            return res
        if not res.get("grounded"):
            return res
        ans = res.get("answer") or ""
        if not ans.strip():
            return res

        # Guardrail 0 (v18): jawaban PERSIS fallback (abstain) -> sembunyikan
        # daftar sumber. Retrieval lemah tetap terjadi saat abstain; menampilkan
        # sumber seolah mendukung jawaban bisa menyesatkan petugas.
        if _flag("RAG_GUARD_FALLBACK_SRC", True):
            fb = (profile.get("fallback") or _rcfg.FALLBACK_DEFAULT or "").strip()
            def _normtxt(s):
                return re.sub(r"\s+", " ", (s or "").strip())
            ansn, fbn = _normtxt(ans), _normtxt(fb)
            if fb and (ansn == fbn or (len(fbn) >= 60 and ansn.startswith(fbn[:60]))):
                if res.get("sources"):
                    res["sources"] = []
                    res["guardrail"] = {"abstain": True,
                                        "alasan": "jawaban fallback (abstain); sumber disembunyikan"}

        # Guardrail 1: tautan tidak resmi / pemendek di body jawaban.
        if _flag("RAG_GUARD_URL", True):
            ans2, removed = _strip_unsafe_urls(ans)
            if removed:
                res["answer"] = ans2
                res["guardrail"] = {"url_dibersihkan": removed}
                ans = ans2

        # Guardrail 2: rujukan hukum tak terdukung -> paksa abstain.
        if _flag("RAG_GUARD_PASAL", True):
            blob = _ctx_blob(konteks) if False else None  # konteks tak diteruskan pemanggil; penilaian memakai sumber res
            blob = _ctx_blob({"_": {"teks": "", "sumber": res.get("sources") or []}})
            tak_terdukung = []
            for jenis, nomor in _refs_in_answer(ans):
                if not _ref_supported(jenis, nomor, blob, {"_": {"teks": "", "sumber": res.get("sources") or []}}):
                    tak_terdukung.append("%s %s" % (jenis, nomor))
            if tak_terdukung:
                res["answer"] = profile.get("fallback") or _rcfg.FALLBACK_DEFAULT
                res["sources"] = []
                res["grounded"] = True
                res["guardrail"] = {"abstain": True,
                                    "alasan": "rujukan hukum tak terdukung: "
                                              + ", ".join(tak_terdukung[:5])}
        return res

    _re.answer = _guarded_answer
    _re._grounding_patched = True
    print("[rag_grounding_patch] guardrail grounding aktif "
          "(url=%s, pasal=%s, abstain-src=%s)."
          % (_flag("RAG_GUARD_URL", True), _flag("RAG_GUARD_PASAL", True),
             _flag("RAG_GUARD_FALLBACK_SRC", True)), flush=True)


_install()
