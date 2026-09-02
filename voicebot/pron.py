# -*- coding: utf-8 -*-
"""voicebot/pron.py -- lapisan pelafalan (normalisasi teks SEBELUM TTS).

TTS membaca teks apa adanya, sehingga singkatan (NIK, NPWP, EFIN) dan angka
panjang (NIK 16 digit, NPWP, nomor telepon) sering salah lafal. Modul ini
menormalkan teks lebih dulu:
  1. Kamus pelafalan (tabel vb_lexicon, bisa diedit di UI): tiap istilah dipetakan
     ke bentuk lisannya -- DIEJA ("NPWP" -> "en pe we pe") atau DIBACA sebagai kata
     ("EFIN" -> "efin").
  2. Angka: deretan digit dengan panjang >= ambang (pron_spell_digits_min) dibaca
     PER DIGIT ("1500200" -> "satu lima nol nol dua nol nol") supaya tidak dibaca
     sebagai bilangan ("satu juta lima ratus ...").

Fail-soft total: error apa pun -> kembalikan teks asli. Dipakai voicebot/tts.py.
"""
import re

_DIGIT = {
    "0": "nol", "1": "satu", "2": "dua", "3": "tiga", "4": "empat",
    "5": "lima", "6": "enam", "7": "tujuh", "8": "delapan", "9": "sembilan",
}


def _spell_digits(run):
    return " ".join(_DIGIT.get(ch, ch) for ch in run)


def _apply_numbers(text, spell_min):
    if not spell_min or spell_min <= 0:
        return text

    def repl(m):
        run = m.group(0)
        return _spell_digits(run) if len(run) >= spell_min else run

    return re.sub(r"\d+", repl, text)


def _apply_lexicon(text, entries):
    for e in entries:
        pat = (e.get("pattern") or "").strip()
        rep = e.get("replacement")
        if not pat or rep is None:
            continue
        try:
            rx = re.compile(r"(?<![0-9A-Za-z])" + re.escape(pat) + r"(?![0-9A-Za-z])",
                            re.IGNORECASE)
            text = rx.sub(lambda _m, r=rep: r, text)
        except Exception:  # noqa: BLE001
            continue
    return text


def normalize(text, entries=None, spell_min=None):
    """Normalkan teks untuk TTS. Fail-soft: kembalikan teks asli bila gagal."""
    text = text or ""
    if not text.strip():
        return text
    try:
        if entries is None:
            from voicebot import config_db as cfg
            entries = cfg.lexicon_map()
        if spell_min is None:
            try:
                from voicebot import config_db as cfg
                spell_min = int(cfg.get_setting("pron_spell_digits_min", "7") or 7)
            except Exception:  # noqa: BLE001
                spell_min = 7
        # istilah lebih panjang diproses lebih dulu (hindari tumpang tindih)
        entries = sorted(entries or [], key=lambda e: len(e.get("pattern") or ""),
                         reverse=True)
        out = _apply_lexicon(text, entries)
        out = _apply_numbers(out, spell_min)
        return out
    except Exception as e:  # noqa: BLE001
        print("[voicebot.pron] normalize gagal: %s" % e, flush=True)
        return text
