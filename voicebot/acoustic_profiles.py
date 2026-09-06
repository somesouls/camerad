# -*- coding: utf-8 -*-
"""voicebot/acoustic_profiles.py -- profil akustik untuk Mode B (streaming barge-in).

Tujuan: membedakan SETELAN barge-in menurut PERANGKAT DENGAR penelepon, tanpa
mengubah algoritma streaming apa pun. Dipakai oleh voicebot/stream.py.

Kenapa perlu beda:
  * LOUDSPEAKER / speakerphone : speaker keras + mic terbuka -> gema/feedback
      tinggi -> ambang barge-in harus TINGGI & konservatif (rawan barge-in palsu).
      Ini profil DEFAULT dan nilainya = setelan yang sudah teruji mulus di ruang
      hening. Profil ini SENGAJA tidak menimpa apa pun (dict kosong) sehingga
      perilaku byte-for-byte identik dengan sebelum fitur profil ada -> NOL regresi.
  * HANDSET / gagang (dekat telinga) : speaker nyaris tak masuk mic -> aman jauh
      lebih responsif: ambang lebih rendah, barge-in lebih cepat.
  * HEADSET / earphone : isolasi terbaik -> paling responsif (mendekati full-duplex).

Prinsip aman:
  * Profil HANYA boleh menimpa subset knob barge-in (whitelist di PROFILE_KEYS).
    Knob lain (VAD, kalibrasi, idle, gate, ducking) TIDAK pernah disentuh profil.
  * Untuk profil isolasi (handset/headset) nilainya hanya MENURUNKAN ambang agar
    lebih responsif -- aman karena tak ada gema bot yang bisa memicu barge-in palsu.
  * Nama profil yang kosong / 'default' / tak dikenal -> diperlakukan sebagai
    loudspeaker (tanpa perubahan). Fail-soft di mana pun.

Sumber pilihan profil (di stream.py): query param WebSocket '?profile=...' (klien),
fallback ke config 'stream_profile'. Nilai preset dapat ditimpa lewat config
'stream_profiles_json' atau ENV VOICEBOT_STREAM_PROFILES_JSON (JSON:
{"handset": {"speaking_rms": 600, ...}, "headset": {...}}), keduanya opsional.
"""
from __future__ import annotations

import os
import json


# Knob yang BOLEH ditimpa profil. Profil tak akan pernah menyentuh knob lain.
PROFILE_KEYS = ("speaking_rms", "bargein_min_ms",
                "bargein_grace_ms", "bargein_hangover_ms")

# Preset bawaan. loudspeaker sengaja KOSONG -> tuning tak disentuh (default).
PRESETS = {
    "loudspeaker": {},
    "handset": {
        "speaking_rms": 600,
        "bargein_min_ms": 350,
        "bargein_grace_ms": 150,
        "bargein_hangover_ms": 150,
    },
    "headset": {
        "speaking_rms": 500,
        "bargein_min_ms": 300,
        "bargein_grace_ms": 80,
        "bargein_hangover_ms": 120,
    },
}

# Alias nama -> profil kanonik ('' berarti loudspeaker/default = tanpa perubahan).
_ALIASES = {
    "": "", "default": "", "loudspeaker": "", "loud": "", "speaker": "",
    "speakerphone": "", "hp": "",
    "handset": "handset", "earpiece": "handset", "gagang": "handset",
    "telinga": "handset", "ear": "handset",
    "headset": "headset", "headphone": "headset", "headphones": "headset",
    "earphone": "headset", "earphones": "headset", "earbud": "headset",
    "earbuds": "headset", "tws": "headset",
}


def normalize(name):
    """Kembalikan profil kanonik ('handset'/'headset') atau '' untuk default."""
    p = (name or "").strip().lower()
    return _ALIASES.get(p, "")


def _load_custom(settings):
    """Muat override preset dari config lalu ENV (JSON). Fail-soft -> {}."""
    out = {}
    for raw in (
        (settings.get("stream_profiles_json") if settings else "") or "",
        os.environ.get("VOICEBOT_STREAM_PROFILES_JSON") or "",
    ):
        if not str(raw).strip():
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        if isinstance(data, dict):
            for k, v in data.items():
                prof = normalize(k)
                if prof and isinstance(v, dict):
                    out.setdefault(prof, {}).update(v)
    return out


def overrides_for(name, settings=None):
    """Dict override {knob: int} untuk profil `name`. {} bila default/tak dikenal.

    Hanya knob whitelist yang dipakai; nilai di-cast ke int dgn aman (fail-soft).
    """
    prof = normalize(name)
    if not prof:
        return {}
    base = dict(PRESETS.get(prof, {}))
    custom = _load_custom(settings)
    if prof in custom:
        for k, v in custom[prof].items():
            if k in PROFILE_KEYS:
                base[k] = v
    clean = {}
    for k in PROFILE_KEYS:
        if k in base and base[k] is not None:
            try:
                clean[k] = int(float(base[k]))
            except Exception:
                pass
    return clean


def apply_to(tuning, name, settings=None):
    """Terapkan preset profil ke dict `tuning` IN-PLACE (hanya knob whitelist).

    Kembalikan (profil_efektif, perubahan) di mana perubahan = {knob: (lama, baru)}.
    Profil default/loudspeaker/tak dikenal -> tak mengubah apa pun -> ('', {}).
    """
    prof = normalize(name)
    if not prof:
        return "", {}
    changes = {}
    for k, v in overrides_for(name, settings).items():
        old = tuning.get(k)
        if old != v:
            tuning[k] = v
            changes[k] = (old, v)
    return prof, changes
