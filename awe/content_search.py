# -*- coding: utf-8 -*-
"""Pencarian isi percakapan bersama (keyword + pencarian spesifik).

Dipakai lintas menu Detail Percakapan (Dialogflow, AWE Chat, AWE Phone) agar
logika pencarian isi seragam & mudah diperluas. Stdlib-only (re, json), tanpa
efek samping - aman diimpor dari mana pun.

Mode pencarian:
  - "keyword"   : semua kata (dipisah spasi) harus muncul sebagai substring
                  (tidak peka huruf). Frasa dalam tanda kutip "..." dicocokkan utuh.
  - "gmail_dot" : email Gmail "trik titik" - local-part mengandung >=1 titik
                  (atau +tag). Gmail mengabaikan titik & +tag sehingga
                  si.nar.jaya@gmail.com == sinarjaya@gmail.com. Berguna untuk
                  mendeteksi calo perubahan data (feeding ke TIK).
  - "email"     : email apa pun (umum).

API utama:
  text_matches(text, query, mode) -> bool
  find_emails(text, gmail_only=False, dot_trick_only=False) -> list[dict]
  normalize_gmail(addr) -> str
  transcript_text(transkrip) -> str
"""
import re
import json as _json

# Regex email yang cukup ketat namun toleran (huruf/angka/._%+- pada local part).
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_GMAIL_DOMAINS = ("gmail.com", "googlemail.com")
_GMAIL_MODES = ("gmail_dot", "gmail-dot", "email_dot", "calo")


def _norm(s):
    return str(s or "").strip().lower()


def normalize_gmail(addr):
    """Bentuk kanonik alamat Gmail (titik & +tag dibuang di local part).

    Untuk domain non-Gmail, kembalikan alamat lowercase apa adanya.
    """
    a = _norm(addr)
    if "@" not in a:
        return a
    local, _, domain = a.partition("@")
    if domain in _GMAIL_DOMAINS:
        local = local.split("+", 1)[0].replace(".", "")
        domain = "gmail.com"  # samakan googlemail -> gmail
    return local + "@" + domain


def _is_gmail(domain):
    return _norm(domain) in _GMAIL_DOMAINS


def find_emails(text, gmail_only=False, dot_trick_only=False):
    """Temukan email pada teks.

    - gmail_only    : hanya domain gmail/googlemail.
    - dot_trick_only: hanya email Gmail yang local-part-nya mengandung titik
                      atau +tag (indikasi trik titik). Mengimplikasikan gmail_only.
    Setiap hasil: {raw, normalized, is_gmail, is_dot_variant}.
    """
    out = []
    seen = set()
    for m in EMAIL_RE.finditer(str(text or "")):
        raw = m.group(0)
        low = raw.lower()
        local, _, domain = low.partition("@")
        isg = _is_gmail(domain)
        dot_variant = isg and ("." in local or "+" in local)
        if (gmail_only or dot_trick_only) and not isg:
            continue
        if dot_trick_only and not dot_variant:
            continue
        if low in seen:
            continue
        seen.add(low)
        out.append({
            "raw": raw,
            "normalized": normalize_gmail(raw),
            "is_gmail": isg,
            "is_dot_variant": dot_variant,
        })
    return out


def _keyword_terms(query):
    """Pecah query jadi term. Dukung frasa dalam tanda kutip ganda."""
    q = str(query or "").strip()
    if not q:
        return []
    terms = []
    for ph in re.findall(r'"([^"]+)"', q):
        t = ph.strip().lower()
        if t:
            terms.append(t)
    rest = re.sub(r'"[^"]+"', " ", q)
    for w in rest.split():
        w = w.strip().lower()
        if w:
            terms.append(w)
    return terms


def keyword_match(text, query):
    """True bila SEMUA term (kata/frasa) muncul sebagai substring (case-insensitive)."""
    terms = _keyword_terms(query)
    if not terms:
        return True
    low = _norm(text)
    return all(t in low for t in terms)


def text_matches(text, query, mode="keyword"):
    """Cocokkan teks dengan query menurut mode. Kembalikan bool."""
    mode = (mode or "keyword").strip().lower()
    if mode in _GMAIL_MODES:
        hits = find_emails(text, dot_trick_only=True)
        if not str(query or "").strip():
            return bool(hits)
        ql = _norm(query)
        return any(ql in h["raw"].lower() or ql in h["normalized"] for h in hits)
    if mode == "email":
        hits = find_emails(text)
        if not str(query or "").strip():
            return bool(hits)
        ql = _norm(query)
        return any(ql in h["raw"].lower() for h in hits)
    return keyword_match(text, query)


def is_specific_mode(mode):
    """True bila mode adalah pencarian spesifik (bukan keyword biasa)."""
    return (mode or "keyword").strip().lower() in (_GMAIL_MODES + ("email",))


def transcript_text(transkrip):
    """Gabungkan list [{role,text}] atau string JSON jadi satu teks utk pencarian."""
    t = transkrip
    if isinstance(t, str):
        s = t.strip()
        if s[:1] in ("[", "{"):
            try:
                t = _json.loads(s)
            except Exception:
                return s
        else:
            return s
    if isinstance(t, list):
        parts = []
        for m in t:
            if isinstance(m, dict):
                parts.append(str(m.get("text") or m.get("teks") or m.get("isi") or ""))
            elif isinstance(m, str):
                parts.append(m)
        return "\n".join(parts)
    return str(t or "")


if __name__ == "__main__":
    # Smoke test (jalankan: python3 content_search.py)
    assert normalize_gmail("si.nar.jaya@gmail.com") == "sinarjaya@gmail.com"
    assert normalize_gmail("sinarjay.a@GMAIL.com") == "sinarjaya@gmail.com"
    assert normalize_gmail("user+promo@googlemail.com") == "user@gmail.com"
    assert normalize_gmail("budi@yahoo.com") == "budi@yahoo.com"
    e = find_emails("kontak si.nar.jaya@gmail.com dan budi@yahoo.com", dot_trick_only=True)
    assert len(e) == 1 and e[0]["normalized"] == "sinarjaya@gmail.com", e
    assert text_matches("ada email si.nar.jaya@gmail.com", "", "gmail_dot") is True
    assert text_matches("email biasa budi@gmail.com", "", "gmail_dot") is False
    assert text_matches("lapor SPT tahunan", "spt tahunan", "keyword") is True
    assert text_matches("lapor SPT", "spt efin", "keyword") is False
    assert keyword_match("Reset Password Coretax", '"reset password"') is True
    assert transcript_text('[{"role":"a","text":"halo"},{"role":"b","text":"dunia"}]') == "halo\ndunia"
    print("CONTENT_SEARCH_SMOKE_OK")
