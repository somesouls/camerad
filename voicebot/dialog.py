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
  - Penjaga diam / silence watchdog (#3): saat penelepon diam di Mode B, sapa
    dulu ('masih terhubung?') lalu akhiri sesi bila tetap tak ada respons.
  - Salam penutup + pemicu (#4): deteksi niat menutup (mis. 'terima kasih')
    dengan GUARD ucapan-berdiri-sendiri + abaikan halusinasi STT, lalu bacakan
    salam penutup APA ADANYA (verbatim) dan tutup.
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


# ------------------------------------ penjaga diam / silence watchdog (#3)
# Khusus Mode B (streaming). Timer diam dijalankan di voicebot/stream.py; helper
# di sini hanya membaca konfigurasi + menyiapkan teksnya (isi {sal}).
def idle_watchdog_enabled(settings):
    """Penjaga diam Mode B aktif? (default ON)."""
    return str((settings or {}).get("stream_idle_enabled", "1")) != "0"


def idle_prompt_ms(settings):
    """Diam berapa ms sebelum bot menyapa 'masih terhubung?' (default 8000)."""
    try:
        n = int(float((settings or {}).get("stream_idle_prompt_ms") or 8000))
    except Exception:
        n = 8000
    return n if n > 0 else 8000


def idle_end_ms(settings):
    """Diam berapa ms LAGI setelah sapaan sebelum sesi diakhiri (default 10000)."""
    try:
        n = int(float((settings or {}).get("stream_idle_end_ms") or 10000))
    except Exception:
        n = 10000
    return n if n > 0 else 10000


def idle_prompt_text(settings):
    """Sapaan saat penelepon mulai diam, mis. 'Halo, apakah masih terhubung, Kak?'"""
    return _fill_sal((settings or {}).get("stream_idle_prompt_text"),
                     settings, "Halo, apakah masih terhubung, {sal}?")


def idle_end_text(settings):
    """Kalimat sebelum sesi ditutup karena tetap tidak ada respons."""
    return _fill_sal((settings or {}).get("stream_idle_end_text"),
                     settings,
                     "Baik, karena belum ada respons, panggilan saya akhiri dulu ya. "
                     "Terima kasih sudah menghubungi kami.")


# ------------------------------------------- salam penutup / closing (#4)
# Selain perintah 'selesai' eksplisit (cmd_end via global_command), penelepon
# kerap menutup dengan ucapan LUNAK seperti 'terima kasih'. Helper di sini
# mendeteksinya DENGAN GUARD + menyaring halusinasi STT. Saat menutup, engine
# membaca closing_reply APA ADANYA (verbatim). State/aksi ditangani engine.
def closing_enabled(settings):
    """Deteksi niat menutup lewat pemicu LUNAK (mis. 'terima kasih') aktif?
    (default ON). Perintah 'selesai' eksplisit tetap jalan lewat global_command
    meski ini dimatikan.
    """
    return str((settings or {}).get("closing_enabled", "1")) != "0"


def closing_max_words(settings):
    """Batas jumlah kata agar ucapan dianggap 'niat menutup' berdiri sendiri."""
    try:
        n = int(float((settings or {}).get("closing_trigger_max_words") or 5))
    except Exception:
        n = 5
    return max(1, n)


def is_stt_hallucination(text, settings):
    """True bila transkrip cocok pola HALUSINASI STT saat senyap (mis.
    'terima kasih telah menonton'). Ucapan seperti ini WAJIB diabaikan:
    jangan dibalas maupun dijadikan pemicu penutup.
    """
    return _contains_any(text, _csv(settings, "closing_hallucination_patterns")) is not None


def wants_closing(text, settings):
    """True bila ucapan menandakan penelepon ingin MENGAKHIRI percakapan.

    GUARD 'terima kasih': pemicu lunak (mis. 'terima kasih', 'makasih',
    'sekian') HANYA dihitung bila ucapan BERDIRI SENDIRI / pendek
    (<= closing_max_words kata). Ini mencegah 'terima kasih' di tengah kalimat
    sopan ("oh terima kasih, tapi saya masih mau tanya ...") memicu penutupan.
    Pola halusinasi STT ('terima kasih telah menonton') disaring lebih dulu.
    Di Mode B, transkrip hanya lahir dari suara yang lolos VAD (energi nyata),
    jadi guard 'energi audio nyata' otomatis terpenuhi.
    """
    if not closing_enabled(settings):
        return False
    tl = _norm(text)
    if not tl:
        return False
    if is_stt_hallucination(tl, settings):
        return False
    if _contains_any(tl, _csv(settings, "closing_triggers")) is None:
        return False
    words = re.findall(r"[0-9a-zA-Z\u00c0-\u024f']+", tl)
    return len(words) <= closing_max_words(settings)


def readback_prompt(text, settings):
    """Readback selektif atas ucapan penelepon (aksi sensitif)."""
    tmpl = (settings or {}).get("readback_template") or (
        "Saya ulangi ya, {text}. Apakah sudah benar?")
    body = tmpl.replace("{text}", (text or "").strip())
    return (_sal_prefix(settings) + body).strip()


def closing_reply(settings):
    """Salam penutup (#4), dibacakan APA ADANYA / verbatim (tanpa peringkas)."""
    return (settings or {}).get("closing_reply") or (
        "Baik, terima kasih sudah menghubungi kami. Selamat beraktivitas kembali.")


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
