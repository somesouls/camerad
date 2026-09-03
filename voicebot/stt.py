# -*- coding: utf-8 -*-
"""voicebot/stt.py -- STT LOKAL (reuse avaya.phone_stt / faster-whisper).

Memakai kembali mesin STT yang sudah ada di proyek (faster-whisper, id-ID) lewat
avaya.phone_stt.transcribe_file. Menerima BYTES audio (dari upload/rekaman),
menulis ke berkas sementara, lalu mentranskrip. Fail-soft: bila STT belum siap
(mis. faster-whisper belum terpasang), kembalikan {ok: False, error}.

STT prediktif / biasing (#5): build_bias() menyusun (initial_prompt, hotwords)
dari konfigurasi + kamus pelafalan (vb_lexicon) + nama intent aktif, lalu
transcribe_bytes meneruskannya ke avaya.phone_stt.transcribe_file supaya dekode
faster-whisper condong ke KOSAKATA DOMAIN (NPWP/EFIN/SPT/dll.). Semua fail-soft:
bila biasing gagal disusun, STT tetap jalan tanpa bias.
"""
import os
import re
import tempfile


def available():
    try:
        import avaya.phone_stt  # noqa: F401
        return True
    except Exception:
        return False


def build_bias(settings=None, conn=None):
    """Susun (initial_prompt, hotwords) domain untuk STT prediktif (#5).

    Sumber istilah (digabung, dedup, dibatasi stt_bias_max_terms):
      - stt_bias_terms   : istilah manual (dipisah koma/baris),
      - kamus pelafalan  : pola vb_lexicon (bila stt_bias_from_lexicon),
      - nama intent aktif: (bila stt_bias_from_intents).
    initial_prompt = stt_bias_prompt (teks domain) + "Istilah penting: <daftar>".
    hotwords       = daftar istilah dipisah koma.
    Kembalikan (None, None) bila biasing dimatikan / gagal / kosong.
    """
    try:
        from voicebot import config_db as cfg
        s = settings or cfg.get_settings(conn=conn)
    except Exception:
        return None, None
    try:
        if str(s.get("stt_bias_enabled", "1")) == "0":
            return None, None
        terms = []
        for t in re.split(r"[,\n;]+", str(s.get("stt_bias_terms") or "")):
            t = t.strip()
            if t:
                terms.append(t)
        if str(s.get("stt_bias_from_lexicon", "1")) != "0":
            try:
                for m in cfg.lexicon_map(conn=conn):
                    p = (m.get("pattern") or "").strip()
                    if p:
                        terms.append(p)
            except Exception:
                pass
        if str(s.get("stt_bias_from_intents", "1")) != "0":
            try:
                for it in cfg.list_intents(conn=conn):
                    nm = (it.get("name") or "").strip()
                    if nm:
                        terms.append(nm)
            except Exception:
                pass
        seen, uniq = set(), []
        for t in terms:
            k = t.lower()
            if k and k not in seen:
                seen.add(k)
                uniq.append(t)
        try:
            cap = int(s.get("stt_bias_max_terms") or 64)
        except Exception:
            cap = 64
        if cap > 0:
            uniq = uniq[:cap]
        prompt = str(s.get("stt_bias_prompt") or "").strip()
        if uniq:
            joined = ", ".join(uniq)
            prompt = ((prompt + " ") if prompt else "") + "Istilah penting: " + joined + "."
        hotwords = ", ".join(uniq) if uniq else None
        prompt = prompt or None
        if not prompt and not hotwords:
            return None, None
        return prompt, hotwords
    except Exception as e:  # noqa: BLE001
        print("[voicebot.stt] build_bias gagal: %s" % e, flush=True)
        return None, None


def transcribe_bytes(data, filename="audio.wav", lang=None,
                     initial_prompt=None, hotwords=None):
    lang = (lang or os.environ.get("AWE_STT_LANG") or "id").strip() or "id"
    if not data:
        return {"ok": False, "text": "", "error": "audio kosong"}
    try:
        import avaya.phone_stt as avstt
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "text": "", "error": "STT lokal belum siap: %s" % e}
    ext = os.path.splitext(filename or "")[1] or ".wav"
    tmp = tempfile.NamedTemporaryFile(prefix="vb_stt_", suffix=ext, delete=False)
    try:
        tmp.write(data)
        tmp.flush()
        tmp.close()
        try:
            tr = avstt.transcribe_file(tmp.name, lang=lang,
                                       initial_prompt=initial_prompt, hotwords=hotwords)
        except TypeError:
            # avaya.phone_stt versi lama tanpa dukungan biasing -> panggil tanpa bias.
            tr = avstt.transcribe_file(tmp.name, lang=lang)
        if not tr or not tr.get("ok"):
            return {"ok": False, "text": "",
                    "error": (tr or {}).get("error") or "STT gagal"}
        return {"ok": True, "text": (tr.get("text") or "").strip(),
                "duration": tr.get("duration"), "device": tr.get("device")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "text": "", "error": str(e)}
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass
