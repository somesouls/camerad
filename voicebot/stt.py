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

Pagar batas token (#8): dekoder Whisper hanya punya 448 posisi token. Bila
initial_prompt + hotwords terlalu panjang (mis. kamus pelafalan / intent sangat
banyak), CTranslate2 melempar RuntimeError('No position encodings are defined
for positions >= 448, ...') dan SEMUA transkripsi gagal -- gejalanya voicebot
'budeg' padahal audio bagus (transkrip selalu kosong + stt_error). Pagar #8:
  (a) build_bias membatasi TOTAL KARAKTER prompt & hotwords (anggaran di bawah),
  (b) stt_bias_max_terms <= 0 tidak lagi berarti 'tanpa batas',
  (c) transcribe_bytes otomatis MENGULANG TANPA bias bila error 448 tetap muncul,
      supaya transkripsi tidak pernah mati total hanya karena bias kepanjangan,
  (d) nama intent gaya Dialogflow ('Layanan Administrasi_EFIN_Lupa EFIN_...')
      DIPECAH per segmen '_' / '/' menjadi frasa pendek alami (EFIN, Lupa EFIN,
      Belum Aktivasi, ...) lalu didedup -- bias lebih efektif & hemat anggaran.
"""
import os
import re
import tempfile

# --- Pagar token (#8) --------------------------------------------------------
# Perkiraan kasar ~4 karakter per token utk teks Indonesia. Prompt + hotwords +
# token kontrol + hasil dekode HARUS < 448 posisi; anggaran di bawah menyisakan
# ruang aman yang lega untuk hasil dekode.
_PROMPT_BASE_MAX = 240       # ~60 token utk teks stt_bias_prompt
_PROMPT_CHAR_BUDGET = 480    # ~120 token utk daftar istilah pada initial_prompt
_HOTWORDS_CHAR_BUDGET = 240  # ~60 token utk hotwords


def available():
    try:
        import avaya.phone_stt  # noqa: F401
        return True
    except Exception:
        return False


def _take_within_budget(items, budget):
    """Ambil item dari depan selama total karakter (plus pemisah ', ') <= budget (#8)."""
    out, tot = [], 0
    for t in items:
        add = len(t) + 2
        if tot + add > budget:
            break
        out.append(t)
        tot += add
    return out


def build_bias(settings=None, conn=None):
    """Susun (initial_prompt, hotwords) domain untuk STT prediktif (#5).

    Sumber istilah (digabung, dedup, dibatasi stt_bias_max_terms):
      - stt_bias_terms   : istilah manual (dipisah koma/baris) -- PRIORITAS TERTINGGI,
      - kamus pelafalan  : pola vb_lexicon (bila stt_bias_from_lexicon),
      - nama intent aktif: dipecah per segmen '_'/'/' (bila stt_bias_from_intents).
    initial_prompt = stt_bias_prompt (teks domain) + \"Istilah penting: <daftar>\".
    hotwords       = daftar istilah dipisah koma.
    Kembalikan (None, None) bila biasing dimatikan / gagal / kosong.

    Pagar (#8): total karakter prompt & hotwords DIBATASI agar tidak menabrak
    batas 448 posisi token dekoder Whisper (lihat header modul).
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
                    if not nm:
                        continue
                    # (#8d) Nama intent gaya Dialogflow dipecah per segmen '_'/'/'
                    # supaya bias berisi frasa alami yang pendek (EFIN, Lupa EFIN,
                    # Belum Aktivasi, ...) dan tidak menghabiskan anggaran karakter.
                    for seg in re.split(r"[_/]+", nm):
                        seg = seg.strip()
                        if 2 <= len(seg) <= 40:
                            terms.append(seg)
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
        if cap <= 0:
            # (#8) 0/negatif TIDAK lagi berarti 'tanpa batas' -- tanpa batas membuat
            # prompt menabrak 448 posisi token dan mematikan STT total.
            cap = 64
        uniq = uniq[:cap]
        base = str(s.get("stt_bias_prompt") or "").strip()
        if len(base) > _PROMPT_BASE_MAX:
            base = base[:_PROMPT_BASE_MAX].rstrip()
        picked = _take_within_budget(uniq, _PROMPT_CHAR_BUDGET)
        prompt = base
        if picked:
            joined = ", ".join(picked)
            prompt = ((base + " ") if base else "") + "Istilah penting: " + joined + "."
        hot_picked = _take_within_budget(uniq, _HOTWORDS_CHAR_BUDGET)
        hotwords = ", ".join(hot_picked) if hot_picked else None
        prompt = prompt or None
        if not prompt and not hotwords:
            return None, None
        if len(picked) < len(uniq) or len(hot_picked) < len(uniq):
            print("[voicebot.stt] bias dipangkas (#8): %d istilah -> prompt %d istilah, "
                  "hotwords %d istilah (jaga batas 448 token Whisper)."
                  % (len(uniq), len(picked), len(hot_picked)), flush=True)
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
    if initial_prompt or hotwords:
        print("[voicebot.stt] bias STT aktif: prompt~%d kar, hotwords~%d kar."
              % (len(initial_prompt or ""), len(hotwords or "")), flush=True)
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
        err = "" if (tr and tr.get("ok")) else str((tr or {}).get("error") or "STT gagal")
        # Fail-soft (#8): bias membuat dekoder melewati 448 posisi token Whisper
        # ('No position encodings are defined for positions >= 448') -> ULANGI
        # TANPA bias supaya transkripsi tetap jalan (lebih baik tanpa bias daripada
        # budeg total).
        if err and (initial_prompt or hotwords) and "position encodings" in err.lower():
            print("[voicebot.stt] bias memicu batas 448 token -> ulangi TANPA bias (#8).",
                  flush=True)
            tr = avstt.transcribe_file(tmp.name, lang=lang)
            err = "" if (tr and tr.get("ok")) else str((tr or {}).get("error") or "STT gagal")
        if err:
            return {"ok": False, "text": "", "error": err}
        return {"ok": True, "text": (tr.get("text") or "").strip(),
                "duration": tr.get("duration"), "device": tr.get("device")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "text": "", "error": str(e)}
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass
