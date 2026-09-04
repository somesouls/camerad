# -*- coding: utf-8 -*-
"""voicebot/sentiment.py -- analisis SENTIMEN & VALENSI ringan (berbasis leksikon).

Tujuan (Poin 3.3): memberi engine sinyal murah tentang SUASANA penelepon supaya
cara menjawab bisa menyesuaikan -- mis. bila penelepon terdengar FRUSTRASI, bot
bisa lebih empatik dan menawarkan agen lebih cepat; bila netral/positif tetap
ringkas dan efisien. Ini SENGAJA ringan (tanpa model berat / tanpa jaringan):
  - Leksikon kata positif & negatif Bahasa Indonesia (layanan pelanggan).
  - Penanganan sederhana untuk NEGASI ('tidak jelas' -> negatif) dan
    INTENSIFIER ('lama banget' -> bobot lebih besar).
  - Frasa-frasa frustrasi kuat ('dari tadi', 'kok gitu', 'buang-buang waktu')
    yang langsung menandai frustrated=True.

Keluaran analyze():
  {
    'label'     : 'neg' | 'neu' | 'pos',
    'valence'   : float [-1..1]  (arah perasaan),
    'magnitude' : float >= 0     (kekuatan sinyal, makin besar makin yakin),
    'frustrated': bool,          (butuh penanganan empatik / tawar agen),
    'pos'/'neg' : bobot mentah (untuk diagnosa),
  }

MURNI & FAIL-SOFT: tidak menyimpan state, tidak melempar exception ke pemanggil
(selalu kembalikan dict netral bila ada masalah). Leksikon default dapat DIPERLUAS
(bukan diganti) lewat setting `sentiment_pos_words` / `sentiment_neg_words` (CSV).
Ambang & perilaku diatur engine; modul ini hanya menghitung.
"""
import re


def _norm(t):
    return (t or "").strip().lower()


# --------------------------------------------------------------- leksikon dasar
# Kata bernuansa NEGATIF khas keluhan layanan (frustrasi/marah/kecewa/kesulitan).
_NEG_DEFAULT = [
    "marah", "kesal", "kesel", "jengkel", "sebal", "sebel", "geram", "emosi",
    "bete", "bt", "dongkol", "kecewa", "mengecewakan", "parah", "payah",
    "buruk", "jelek", "gagal", "error", "rusak", "ngaco", "kacau", "bingung",
    "pusing", "capek", "capai", "lelah", "males", "malas", "ribet", "rumit",
    "susah", "sulit", "lama", "lambat", "lelet", "bertele-tele", "berbelit",
    "mahal", "keluhan", "komplain", "protes", "nyebelin", "ganggu", "gangguan",
    "bohong", "dibohongi", "tipu", "penipuan", "katanya", "janji", "mengulang",
    "muter", "puter", "lambannya", "lemot",
]

# Kata bernuansa POSITIF (puas/terbantu/ramah).
_POS_DEFAULT = [
    "terima kasih", "makasih", "terimakasih", "mantap", "bagus", "baik", "oke",
    "okay", "keren", "puas", "senang", "membantu", "terbantu", "cepat", "jelas",
    "ramah", "sip", "hebat", "top", "lancar", "memuaskan", "enak", "nyaman",
    "mudah", "gampang", "paham", "mengerti",
]

# Kata pengingkar (membalik polaritas kata setelahnya dalam jendela pendek).
_NEGATORS = [
    "tidak", "tak", "tdk", "bukan", "belum", "gak", "nggak", "ngga", "ga",
    "enggak", "kagak", "jangan",
]

# Penguat (menambah bobot kata sentimen yang mengikutinya).
_INTENSIFIERS = [
    "sangat", "banget", "sekali", "amat", "benar-benar", "bener-bener",
    "sungguh", "terlalu", "kelewat", "super", "paling",
]

# Frasa frustrasi KUAT -> langsung tandai frustrated (regex kata-utuh longgar).
_STRONG_NEG_PHRASES = [
    r"dari tadi", r"kok gitu", r"kok begitu", r"gimana sih", r"gmn sih",
    r"capek deh", r"cape deh", r"buang[ -]?buang waktu", r"bung[ -]?buang waktu",
    r"muter[ -]?muter", r"puter[ -]?puter", r"berkali[ -]?kali",
    r"gak jelas", r"nggak jelas", r"ga jelas", r"tidak jelas",
    r"lama banget", r"lelet banget", r"lemot banget", r"parah banget",
]


def _extra_terms(settings, key):
    """Ambil daftar tambahan (CSV) dari setting; kosong bila tak ada. Fail-soft."""
    try:
        raw = str((settings or {}).get(key, "") or "")
    except Exception:
        raw = ""
    return [s.strip().lower() for s in raw.split(",") if s.strip()]


def _lexicons(settings):
    pos = set(_POS_DEFAULT) | set(_extra_terms(settings, "sentiment_pos_words"))
    neg = set(_NEG_DEFAULT) | set(_extra_terms(settings, "sentiment_neg_words"))
    return pos, neg


def _neg_threshold(settings):
    """Magnitude minimal agar label 'neg' dianggap FRUSTRASI (default 1.5)."""
    try:
        return float((settings or {}).get("sentiment_frustrated_min") or 1.5)
    except Exception:
        return 1.5


def _valence_cut(settings):
    """Ambang |valence| untuk melabeli neg/pos (default 0.34)."""
    try:
        return float((settings or {}).get("sentiment_valence_cut") or 0.34)
    except Exception:
        return 0.34


def _strong_hit(text, settings):
    tl = _norm(text)
    for pat in _STRONG_NEG_PHRASES:
        try:
            if re.search(pat, tl):
                return True
        except Exception:
            continue
    return False


def _neutral():
    return {"label": "neu", "valence": 0.0, "magnitude": 0.0,
            "frustrated": False, "pos": 0.0, "neg": 0.0}


def analyze(text, settings=None):
    """Analisis sentimen/valensi satu ucapan. Selalu kembalikan dict (fail-soft)."""
    try:
        t = _norm(text)
        if not t:
            return _neutral()
        posset, negset = _lexicons(settings)

        # cocokkan frasa positif multi-kata (mis. 'terima kasih') lebih dulu:
        # hitung sebagai sinyal positif, lalu buang agar tak dihitung ganda.
        pos_w = 0.0
        for phr in [p for p in posset if " " in p]:
            if phr in t:
                pos_w += 1.0
                t = t.replace(phr, " ")

        toks = re.findall(r"[a-z0-9\u00c0-\u024f'-]+", t)
        neg_w = 0.0
        for i, w in enumerate(toks):
            polarity = 0
            if w in posset:
                polarity = 1
            elif w in negset:
                polarity = -1
            if polarity == 0:
                continue
            mult = 1.0
            if i > 0 and toks[i - 1] in _INTENSIFIERS:
                mult = 1.6
            # negasi dalam 2 token sebelum kata sentimen -> balik polaritas
            flipped = False
            for j in range(max(0, i - 2), i):
                if toks[j] in _NEGATORS:
                    flipped = True
                    break
            eff = polarity * (-1 if flipped else 1)
            if eff > 0:
                pos_w += mult
            else:
                neg_w += mult

        strong = _strong_hit(text, settings)
        if strong:
            neg_w += 2.0

        total = pos_w + neg_w
        valence = 0.0 if total <= 0 else (pos_w - neg_w) / total
        cut = _valence_cut(settings)
        if strong or valence <= -cut:
            label = "neg"
        elif valence >= cut:
            label = "pos"
        else:
            label = "neu"
        frustrated = bool(label == "neg"
                          and (total >= _neg_threshold(settings) or strong))
        return {
            "label": label,
            "valence": round(valence, 3),
            "magnitude": round(total, 3),
            "frustrated": frustrated,
            "pos": round(pos_w, 3),
            "neg": round(neg_w, 3),
        }
    except Exception:
        return _neutral()


def enabled(settings):
    """Apakah analisis sentimen diaktifkan? Setting `sentiment_enabled` (default '0' = mati)."""
    try:
        return str((settings or {}).get("sentiment_enabled", "0")) not in (
            "0", "false", "False", "no", "NO", "")
    except Exception:
        return False
