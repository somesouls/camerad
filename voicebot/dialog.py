# -*- coding: utf-8 -*-
"""voicebot/dialog.py -- dialog manager voicebot (di atas NLU/RAG).

Lapisan keputusan percakapan bergaya agen:
  - Perintah global (selalu aktif): ulangi / selesai / bicara dengan agen.
  - Tier confidence: 'act' (>= ambang) / 'confirm' (menengah) / 'rag' (rendah).
  - Konfirmasi selektif + resolusi jawaban ya/tidak (state pending_confirm).
  - Konfirmasi-dulu (#1): saat intent ditemukan, baca ulang kalimat konfirmasi
    deterministik (tanpa LLM) lalu siapkan jawaban di background.
  - Jawaban menuntun / guided walkthrough (#2): sampaikan jawaban panjang
    BERTAHAP (satu langkah tiap giliran) + tawar agen bila penelepon buntu.
  - Digression: deteksi pindah intent + tawaran resume intent sebelumnya.
  - Readback selektif, sapaan 'Kak', dan teks filler.

Semua fungsi murni & fail-soft; STATE percakapan dipegang engine (per sesi).
Engine memanggil helper di sini; modul ini tidak menyimpan state sendiri.
"""
import re


def _norm(t):
    return (t or "").strip().lower()


def _csv(settings, key, default=""):
    raw = str((settings or {}).get(key, default) or "")
    return [s.strip().lower() for s in raw.split(",") if s.strip()]


def _contains_any(text, terms):
    """Kembalikan istilah pertama yang cocok sebagai kata utuh, atau None."""
    tl = _norm(text)
    if not tl:
        return None
    for t in terms:
        if not t:
            continue
        if re.search(r"(?<![0-9a-z])" + re.escape(t) + r"(?![0-9a-z])", tl):
            return t
    return None


def enabled(settings):
    return str((settings or {}).get("dialog_enabled", "1")) != "0"


def threshold(settings):
    try:
        return float((settings or {}).get("threshold") or 0.6)
    except Exception:
        return 0.6


def confirm_min(settings):
    try:
        return float((settings or {}).get("confirm_min") or 0.45)
    except Exception:
        return 0.45


def decide_tier(confidence, settings):
    """'act' | 'confirm' | 'rag' berdasar confidence terhadap ambang.

    act    : confidence >= threshold            -> jawaban intent (deterministik)
    confirm: confirm_min <= confidence < thr    -> konfirmasi/klarifikasi
    rag    : confidence < confirm_min           -> RAG bersumber intent
    """
    thr = threshold(settings)
    cmin = confirm_min(settings)
    if cmin > thr:
        cmin = thr
    try:
        c = float(confidence or 0.0)
    except Exception:
        c = 0.0
    if c >= thr:
        return "act"
    if c >= cmin:
        return "confirm"
    return "rag"


# ------------------------------------------------------------ perintah global
def global_command(text, settings):
    """Kembalikan 'repeat' | 'end' | 'handoff' | None (selalu aktif)."""
    if not enabled(settings):
        return None
    if _contains_any(text, _csv(settings, "cmd_repeat")):
        return "repeat"
    if _contains_any(text, _csv(settings, "cmd_end")):
        return "end"
    if _contains_any(text, _csv(settings, "handoff_triggers")):
        return "handoff"
    return None


def is_affirmative(text, settings):
    return _contains_any(text, _csv(settings, "affirmations")) is not None


def is_negative(text, settings):
    return _contains_any(text, _csv(settings, "negations")) is not None


# ------------------------------------------------------------ sapaan & prompt
def salutation(settings):
    return (str((settings or {}).get("salutation") or "Kak")).strip()


def _sal_prefix(settings):
    if str((settings or {}).get("salutation_enabled", "1")) == "0":
        return ""
    s = salutation(settings)
    return ("Baik, %s. " % s) if s else "Baik. "


def _sal_value(settings):
    """Nilai sapaan efektif ('' bila sapaan dinonaktifkan)."""
    if str((settings or {}).get("salutation_enabled", "1")) == "0":
        return ""
    return salutation(settings)


def _fill_sal(tmpl, settings, default=""):
    """Isi {sal} pada template lalu rapikan spasi/tanda baca menggantung."""
    body = (tmpl or default).replace("{sal}", _sal_value(settings))
    body = re.sub(r"\s+", " ", body).replace(" ,", ",").replace(" .", ".")
    return body.strip()


def confirm_prompt(intent, settings):
    """Prompt konfirmasi selektif untuk tier menengah."""
    tmpl = (settings or {}).get("confirm_template") or (
        "Mohon konfirmasi, apakah Anda menanyakan tentang {intent}?")
    body = tmpl.replace("{intent}", intent or "hal tersebut")
    return (_sal_prefix(settings) + body).strip()


# ------------------------------------------------------- konfirmasi-dulu (#1)
def confirm_first_enabled(settings):
    """Konfirmasi-dulu tanpa LLM aktif? (default ON)."""
    return str((settings or {}).get("confirm_first", "1")) != "0"


def auto_confirm_label(intent_name):
    """Fallback kalimat konfirmasi bila confirm_label intent kosong.

    Contoh: 'Layanan Administrasi_EFIN_Lupa EFIN' -> segmen paling spesifik
    'Lupa EFIN' -> 'apakah benar mengenai Lupa EFIN?'.
    """
    s = (intent_name or "").strip()
    if not s:
        return "apakah benar seperti itu?"
    parts = [p.strip() for p in s.split("_") if p.strip()]
    core = parts[-1] if len(parts) > 1 else s
    core = re.sub(r"\s+", " ", core.replace("_", " ")).strip()
    return "apakah benar mengenai %s?" % core


def confirm_first_prompt(label, settings):
    """Kalimat konfirmasi deterministik di giliran pertama (tanpa LLM).

    Template default: 'Baik {sal}, saya konfirmasi, {label}'.
    {sal}=sapaan (mis. 'Kak'), {label}=confirm_label intent / fallback.
    """
    tmpl = (settings or {}).get("confirm_first_template") or (
        "Baik {sal}, saya konfirmasi, {label}")
    if str((settings or {}).get("salutation_enabled", "1")) == "0":
        sal = ""
    else:
        sal = salutation(settings)
    body = tmpl.replace("{sal}", sal).replace("{label}", (label or "").strip())
    body = re.sub(r"\s+", " ", body).replace(" ,", ",").strip()
    return body


# ------------------------------------------ jawaban menuntun / guided (#2)
def guided_enabled(settings):
    """Jawaban menuntun bertahap aktif? (default ON)."""
    return str((settings or {}).get("guided_enabled", "1")) != "0"


def guided_min_steps(settings):
    """Minimal jumlah langkah agar jawaban disampaikan bertahap (>= 2)."""
    try:
        n = int((settings or {}).get("guided_min_steps") or 2)
    except Exception:
        n = 2
    return max(2, n)


def guided_intro(settings):
    """Pembuka singkat sebelum langkah pertama, mis. 'Baik Kak, saya bantu ya.'"""
    return _fill_sal((settings or {}).get("guided_intro_template"),
                     settings, "Baik {sal}, saya bantu ya.")


def guided_nudge(settings):
    """Dorongan lembut setelah langkah non-terakhir agar penelepon menanggapi."""
    return _fill_sal((settings or {}).get("guided_nudge_template"),
                     settings, "Kalau sudah atau ada kendala, sampaikan saja ya, {sal}.")


def guided_closing(settings):
    """Penutup alur menuntun setelah langkah terakhir."""
    return _fill_sal((settings or {}).get("guided_closing_template"),
                     settings,
                     "Itu tadi langkah-langkahnya, {sal}. Ada lagi yang bisa saya bantu?")


def guided_handoff_offer(settings):
    """Tawaran menghubungkan ke agen saat penelepon buntu di tengah alur."""
    return _fill_sal((settings or {}).get("guided_handoff_offer"),
                     settings,
                     "Mohon maaf {sal}, untuk hal itu sepertinya perlu bantuan petugas kami. "
                     "Mau saya hubungkan dengan agen?")


def wants_handoff_in_flow(text, settings):
    """True bila selaan penelepon menandakan buntu/tak terbantu (tawar agen)."""
    return _contains_any(text, _csv(settings, "guided_handoff_triggers")) is not None


def readback_prompt(text, settings):
    """Readback selektif atas ucapan penelepon (aksi sensitif)."""
    tmpl = (settings or {}).get("readback_template") or (
        "Saya ulangi ya, {text}. Apakah sudah benar?")
    body = tmpl.replace("{text}", (text or "").strip())
    return (_sal_prefix(settings) + body).strip()


def closing_reply(settings):
    return (settings or {}).get("closing_reply") or (
        "Baik, terima kasih sudah menghubungi kami. Semoga harinya menyenangkan.")


def greeting(settings):
    return (settings or {}).get("greeting") or (
        "Selamat datang di layanan kami. Ada yang bisa saya bantu, Kak?")


def handoff_reply(settings):
    return (settings or {}).get("handoff_reply") or (
        "Baik, saya hubungkan Anda dengan agen kami. Mohon tunggu sebentar.")


def resume_enabled(settings):
    return str((settings or {}).get("resume_enabled", "0")) != "0"


def resume_prompt(prev_intent, settings):
    tmpl = (settings or {}).get("resume_template") or (
        "Sebelumnya kita membahas {intent}. Mau lanjutkan itu setelah ini?")
    return tmpl.replace("{intent}", prev_intent or "")


# ------------------------------------------------------------------- filler
def filler_enabled(settings):
    return str((settings or {}).get("filler_enabled", "1")) != "0"


def fillers(settings):
    raw = (settings or {}).get("filler_texts") or ""
    arr = [s.strip() for s in re.split(r"[|\n]+", raw) if s.strip()]
    return arr or ["Baik, mohon tunggu sebentar ya."]
