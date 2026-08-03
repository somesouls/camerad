# -*- coding: utf-8 -*-
"""Masking PII (Fase A) — regex ringan untuk pola Indonesia.

Titik integrasi TERPUSAT: panggil `mask_text(t)` pada teks apa pun sebelum
dikirim ke LLM cloud. Aktif secara default; matikan dengan env `PII_MASKING`
diset ke off/0/false/no.

Fase B (Presidio) nanti cukup mengganti isi `mask_text` (mis. memanggil
AnalyzerEngine+AnonymizerEngine) TANPA mengubah pemanggil di studio_routes.py
& web_app.py.

Tidak ada dependensi eksternal (murni stdlib `re`).
"""
import os
import re

__all__ = ["mask_text", "mask", "masking_enabled", "scan"]


def masking_enabled():
    """True kecuali env PII_MASKING diset ke off/0/false/no."""
    v = (os.environ.get("PII_MASKING", "on") or "").strip().lower()
    return v not in ("off", "0", "false", "no")


# --- Pola PII Indonesia -------------------------------------------------
# Email
_RE_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")

# NPWP lama berformat: 99.999.999.9-999.999 (15 digit dengan pemisah)
_RE_NPWP_FMT = re.compile(r"\b\d{2}\.\d{3}\.\d{3}\.\d-\d{3}\.\d{3}\b")

# NIK / NPWP-baru: tepat 16 digit polos (tanpa digit di kiri/kanan)
_RE_NIK16 = re.compile(r"(?<!\d)\d{16}(?!\d)")

# NPWP lama polos: tepat 15 digit
_RE_NPWP15 = re.compile(r"(?<!\d)\d{15}(?!\d)")

# Nomor HP Indonesia: diawali 08 / +62 / 62, total kira-kira 10-14 digit.
# Boleh ada pemisah spasi/titik/strip antar-grup.
_RE_HP = re.compile(r"(?<![\w+])(?:\+?62|0)8\d(?:[ .\-]?\d){7,11}(?!\w)")

# Urutan penanda diterapkan: format berpola & ID panjang lebih dulu,
# lalu email, lalu HP. (16/15 digit polos dicek sebelum HP agar tidak
# terpotong sebagian oleh pola HP.)
_PIPELINE = [
    (_RE_NPWP_FMT, "<NPWP>"),
    (_RE_EMAIL, "<EMAIL>"),
    (_RE_NIK16, "<NIK>"),
    (_RE_NPWP15, "<NPWP>"),
    (_RE_HP, "<HP>"),
]


def mask_text(text, enabled=None):
    """Kembalikan teks dengan PII diganti penanda (<NIK>, <NPWP>, <HP>, <EMAIL>).

    Aman untuk input non-string (dikembalikan apa adanya).
    """
    if text is None or not isinstance(text, str) or not text:
        return text
    if enabled is None:
        enabled = masking_enabled()
    if not enabled:
        return text
    s = text
    for rx, repl in _PIPELINE:
        s = rx.sub(repl, s)
    return s


# Alias singkat
mask = mask_text


def scan(text):
    """Diagnostik: hitung jumlah temuan per jenis PII (tanpa mengubah teks)."""
    if not text or not isinstance(text, str):
        return {}
    return {
        "NPWP_fmt": len(_RE_NPWP_FMT.findall(text)),
        "EMAIL": len(_RE_EMAIL.findall(text)),
        "NIK16": len(_RE_NIK16.findall(text)),
        "NPWP15": len(_RE_NPWP15.findall(text)),
        "HP": len(_RE_HP.findall(text)),
    }
