# -*- coding: utf-8 -*-
"""voicebot/stt_correct.py -- koreksi domain pasca-STT (#STT-domain).

Setelah STT menghasilkan transkrip, istilah domain yang SERING salah didengar
(mis. 'Coretax' -> 'memkorteks' / 'core tax') dikembalikan ke bentuk baku
SEBELUM NLU/dialog memakainya. Dua lapis, semua fail-soft:

  1) PETA PENGGANTI eksplisit (setting stt_correct_map, fallback _DEFAULT_MAP):
     satu aturan per baris (atau dipisah '|') 'salah dengar => bentuk baku'.
     Cocok huruf-kecil, spasi dinormalkan, hanya pada batas kata supaya tak
     memotong bagian kata lain. Frasa lebih panjang diterapkan lebih dulu.

  2) PENCOCOKAN FUZZY (setting stt_correct_fuzzy): tiap kata transkrip yang MIRIP
     (difflib ratio >= stt_correct_fuzzy_min) dengan salah satu istilah baku
     (stt_correct_terms, fallback _DEFAULT_TERMS) diganti ke istilah baku itu --
     menangkap salah-dengar yang belum terdaftar di peta. Hanya istilah baku >= 5
     huruf & satu kata yang ikut fuzzy (hindari salah-ganti akronim/kata pendek);
     istilah multi-kata/akronim ditangani lewat peta. Kapitalisasi baku dijaga.

Bila sebuah setting tak ada di DB (mis. belum migrasi), dipakai default bawaan
di modul ini, jadi fitur tetap berfungsi. Kosongkan setting untuk menonaktifkan
efek terkait. Dipakai voicebot.engine sesudah STT (hanya jalur AUDIO).
"""
import re
import difflib

_OFF = ("0", "false", "False", "no", "NO", "")

# Default bawaan (dipakai bila setting tak ada di DB). Fokus istilah domain pajak
# yang sering salah didengar; bisa ditimpa dari UI Konfigurasi.
_DEFAULT_MAP = (
    "memkorteks => Coretax\n"
    "mem korteks => Coretax\n"
    "korteks => Coretax\n"
    "kortek => Coretax\n"
    "kortaks => Coretax\n"
    "kortax => Coretax\n"
    "core tax => Coretax\n"
    "koretaks => Coretax\n"
    "koretak => Coretax\n"
    "koretax => Coretax"
)
_DEFAULT_TERMS = (
    "Coretax, NPWP, NIK, EFIN, SPT, PPh, PPN, DJP, KPP, KTP, NITKU, "
    "e-Filing, e-Billing, e-Faktur"
)


def _enabled(settings):
    try:
        return str((settings or {}).get("stt_correct_enabled", "1")) not in _OFF
    except Exception:
        return False


def _setting(settings, key, default):
    v = (settings or {}).get(key)
    if v is None:
        return default
    return v


def _parse_map(raw):
    """Uraikan 'salah => benar' per baris/'|'. Kembalikan list (pola_lc, benar)
    diurut dari pola TERPANJANG dulu supaya frasa menang atas kata tunggal."""
    out = []
    for line in re.split(r"[\n|]+", str(raw or "")):
        line = line.strip()
        if not line or "=>" not in line:
            continue
        left, right = line.split("=>", 1)
        pat = re.sub(r"\s+", " ", left.strip().lower())
        rep = right.strip()
        if pat:
            out.append((pat, rep))
    out.sort(key=lambda x: len(x[0]), reverse=True)
    return out


def _terms(raw):
    out = []
    for t in re.split(r"[,\n|;]+", str(raw or "")):
        t = t.strip()
        if t:
            out.append(t)
    return out


def _apply_map(text, pairs):
    for pat, rep in pairs:
        try:
            rx = re.compile(
                r"\b" + r"\s+".join(re.escape(w) for w in pat.split(" ")) + r"\b",
                re.IGNORECASE,
            )
            text = rx.sub(rep, text)
        except Exception:
            continue
    return text


def _apply_fuzzy(text, terms, min_ratio):
    single = [t for t in terms if " " not in t and "-" not in t and len(t) >= 5]
    if not single:
        return text
    low_to_canon = {}
    for t in single:
        low_to_canon.setdefault(t.lower(), t)
    lows = list(low_to_canon.keys())

    def _repl(m):
        w = m.group(0)
        wl = w.lower()
        if wl in low_to_canon:
            return low_to_canon[wl]
        cand = difflib.get_close_matches(wl, lows, n=1, cutoff=min_ratio)
        if cand:
            return low_to_canon[cand[0]]
        return w

    try:
        return re.sub(r"[A-Za-z\u00c0-\u00ff]+", _repl, text)
    except Exception:
        return text


def correct(text, settings=None):
    """Kembalikan transkrip terkoreksi; fail-soft (kembalikan input bila error)."""
    try:
        if not text or not _enabled(settings):
            return text
        s = settings or {}
        out = text
        pairs = _parse_map(_setting(s, "stt_correct_map", _DEFAULT_MAP))
        if pairs:
            out = _apply_map(out, pairs)
        if str(_setting(s, "stt_correct_fuzzy", "1")) not in _OFF:
            try:
                mn = float(_setting(s, "stt_correct_fuzzy_min", "0.86") or 0.86)
            except Exception:
                mn = 0.86
            if mn > 1.0:
                mn = 1.0
            terms = _terms(_setting(s, "stt_correct_terms", _DEFAULT_TERMS))
            if terms and mn > 0:
                out = _apply_fuzzy(out, terms, mn)
        out = re.sub(r"\s+", " ", out).strip()
        return out or text
    except Exception:
        return text
