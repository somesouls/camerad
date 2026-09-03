# -*- coding: utf-8 -*-
"""voicebot/stream.py -- Mode B: percakapan suara real-time (WebSocket) + barge-in.

Endpoint: WS /api/voicebot/stream

Klien -> server:
  * biner  : frame audio PCM16 mono little-endian, sample rate 16 kHz.
  * teks (JSON):
      {"type":"hello","want_audio":true,"bargein":true?}
      {"type":"bargein","on":true|false}   -> set barge-in via mic (ikut centang UI)
      {"type":"playing","on":true|false}   -> klien SEDANG/berhenti memutar audio bot
      {"type":"barge_in"}                    -> paksa hentikan audio bot (tombol Potong)
      {"type":"flush"}                       -> paksa akhiri ucapan sekarang
      {"type":"bye"}                         -> tutup sesi

Server -> klien:
  * teks (JSON): ready, speech_start, speech_candidate, speech_cancel, thinking,
      no_speech, transcript, answer, idle_prompt, idle_end, audio_begin,
      audio_end, no_audio, interrupted, error.
  * biner  : potongan byte WAV jawaban (antara audio_begin & audio_end).

=== PERBAIKAN UTAMA (agar jawaban tuntas & hanya berhenti bila benar disela) ===

1) JENDELA 'BOT BICARA' MENGIKUTI PEMUTARAN DI KLIEN.
   Klien menyangga audio lalu MEMUTARNYA setelah audio_end, jadi periode bot
   benar-benar bersuara ada DI SISI KLIEN. Klien mengirim {"type":"playing",on}
   saat mulai/berhenti memutar. Server menganggap bot bicara bila: sedang
   mengirim byte (state.speaking) ATAU klien sedang memutar (state.client_playing)
   ATAU masih dalam jeda-aman singkat (speak_guard) sesudah mengirim. Dengan ini
   gema/noise saat pemutaran TIDAK lagi dianggap ucapan user -> jawaban tak lagi
   'berhenti di tengah' oleh noise.

2) CENTANG 'BARGE-IN VIA MIC' BENAR-BENAR MENGONTROL SERVER.
   Centang di UI dikirim ke server (pesan 'bargein'/'hello'). Bila MATI, server
   half-duplex: mic DIABAIKAN total selama bot bicara (tak mungkin dipotong noise;
   tombol Potong tetap bisa). Bila HIDUP, barge-in hanya dari suara BERKELANJUTAN.

3) PENJAGA GEMA: saat bot bicara, frame dianggap 'bicara' HANYA bila webrtcvad
   bilang bicara DAN energi (rms) >= speaking_rms. Barge-in butuh bicara
   berkelanjutan >= bargein_min_ms. Saat memverifikasi -> ducking volume bot.

4) ANTI-NOISE ADAPTIF #6 (diperbaiki agar TIDAK 'budeg'):
   - Lantai noise adaptif dibatasi (cap) dan HANYA belajar dari frame yang
     webrtcvad pastikan BUKAN bicara -> ucapan asli tak pernah menaikkan lantai
     (dulu bisa 'runaway' makin lama makin tuli).
   - Ambang energi efektif = clamp(max(base, floor*snr), base .. base*CAP).
   - Auto-kalibrasi (stream_autocalibrate): server mengukur energi ambient di
     ~calib_ms awal (saat bot diam) lalu menyetel lantai noise otomatis, jadi
     tak perlu tebak-tebak angka manual tiap lingkungan.
   - Frame onset berurutan (stream_onset_frames) + rasio frame bersuara
     (stream_voiced_ratio_min) tetap membuang klik/ketikan/pop & segmen dominan noise.

5) ANTI-BUDEG LANJUTAN (perbaikan 3 Sep, malam):
   a. RASIO BERSUARA DIHITUNG LONGGAR (vad OR energi). Dulu sebuah frame hanya
      dihitung 'bersuara' bila webrtcvad bilang bicara DAN energinya >= ambang.
      Frame lirih (akhir kata, konsonan tak bersuara, suara pelan) tidak lolos
      syarat ganda itu, sehingga ucapan ASLI sering dibuang sebagai 'noise
      dominan' (rasio 0.24-0.34 < 0.35) -> tidak pernah ada transkrip = budeg.
      Kini rasio memakai OR (vad ATAU energi); gerbang trigger/endpointing
      tetap ketat (AND) supaya deteksi awal bicara tidak jadi sensitif noise.
   b. UCAPAN YANG DIBUANG TIDAK LAGI SENYAP: server kirim {"type":"no_speech"}
      + alasan, supaya UI tidak menggantung 'memproses' tanpa kabar.
   c. UCAPAN PENYELA (BARGE-IN) IKUT DIPROSES. Dulu ucapan user yang memotong
      bot DIBUANG begitu saja (user harus mengulang bicara -> terasa budeg).
      Kini saat barge-in terkonfirmasi, ucapan terus direkam dan begitu selesai
      langsung dikirim ke STT/NLU seperti ucapan biasa.
   d. FLAG interrupt DIBERSIHKAN setelah audio bot benar-benar dipotong. Dulu
      flag basi membuat audio JAWABAN BERIKUTNYA ikut di-skip (jawaban ada
      teksnya tapi tak pernah dibacakan).

6) DIAGNOSA PRESISI + AMBANG ADAPTIF MIC PELAN (perbaikan 3 Sep, larut malam):
   a. VERSI KODE dicatat di log saat sesi dibuka -> memastikan kode yang jalan
      adalah kode terbaru (bukan sisa deploy lama).
   b. TELEMETRI MIC ~1x/detik (stream_debug, default AKTIF): jumlah frame,
      rms rata2/maks, jumlah frame sunyi total (indikasi gerbang browser
      menahan audio -> klien mengirim frame nol), jumlah frame lolos webrtcvad,
      jumlah frame lolos ambang energi, ambang efektif & lantai noise, status
      talking/trigger. Dengan ini penyebab 'budeg' terlihat PASTI dari log:
      - rms maks tinggi tapi 'ketat' 0            -> ambang energi kebesaran;
      - hampir semua frame 'sunyi total'          -> browser menahan audio;
      - tidak ada telemetri sama sekali           -> frame tak sampai server;
      - trigger jalan tapi tak ada 'SELESAI->STT' -> masalah endpointing;
      - 'SELESAI->STT' ada tapi transkrip kosong  -> masalah STT.
   c. AMBANG DENGAR ADAPTIF UNTUK MIC PELAN (anti-budeg #7). stream_rms bersifat
      ABSOLUT (default 600), padahal level mic tiap PC beda jauh. Pada mic pelan
      (contoh nyata: ambient ~45-76, ucapan ~200-500) ambang 600 TAK PERNAH
      tertembus -> endpointer tak pernah trigger -> budeg total tanpa log apa pun.
      Kini SAAT MENDENGAR (bukan saat bot bicara) ambang efektif juga dibatasi
      dari atas: thr = min(thr_lama, max(lantai_noise*QUIET_SNR_MULT, ABS_MIN_THR)).
      Lingkungan normal (lantai >= ~150) tak berubah; mic pelan otomatis peka.
      Penjaga gema saat bot bicara (speaking_rms) SENGAJA tetap absolut.
   d. Log tambahan: utterance selesai/terlalu pendek, utterance tertelan jendela
      'bot bicara', hasil STT/NLU (transkrip, intent, sumber, elapsed), antrean,
      dan playing on/off dari klien.

PENJAGA DIAM #3 & SALAM PENUTUP #4: tak berubah perilakunya.

SEMUA TUNING DAPAT DIATUR DARI UI (/voicebot, panel \"Streaming (Mode B) & barge-in\";
tersedia tombol \"Reset ke rekomendasi\"). Kunci config -> (ENV lama, default):
  stream_silence_ms(700) stream_min_speech_ms(350) stream_preroll_ms(300)
  stream_rms(600) stream_vad_aggr(3) stream_bargein(1) stream_bargein_min_ms(500)
  stream_speaking_rms(900) stream_gate_rms(0.012) stream_gate_hangover_ms(600)
  stream_ducking(1) stream_duck_gain(0.2) stream_noise_adapt(1) stream_snr_ratio(1.8)
  stream_noise_floor_init(150) stream_onset_frames(3) stream_voiced_ratio_min(0.35)
  stream_autocalibrate(1) stream_calib_ms(1200) stream_mic_hangover_ms(250)
  stream_idle_enabled(1) stream_idle_prompt_ms(8000) stream_idle_end_ms(10000)
  stream_debug(1)
Perubahan berlaku untuk sesi streaming BERIKUTNYA (buka ulang percakapan Mode B).
"""
from __future__ import annotations

import os
import io
import json
import time
import wave
import array
import base64
import asyncio

from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from voicebot import engine as vb_engine
from voicebot import tts as vb_tts
from voicebot import dialog as vb_dialog
from voicebot import config_db as cfg


# Versi kode; dicatat di log tiap sesi dibuka supaya PASTI kode terbaru yang jalan.
STREAM_VERSION = "2026-09-03d (diagnosa + anti-budeg #7 mic pelan)"

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_BYTES = int(SAMPLE_RATE * FRAME_MS / 1000) * 2  # 960 byte / frame 30ms

# Lantai noise adaptif tak boleh menaikkan ambang lebih dari CAP x ambang dasar
# supaya tak pernah 'budeg' terhadap ucapan asli di lingkungan berisik.
NOISE_FLOOR_CAP_MULT = 4.0
# #7: pada MIC PELAN, ambang absolut stream_rms bisa jauh di atas level suara mic.
# Saat mendengar, ambang efektif dibatasi dari atas oleh lantai_noise*QUIET_SNR_MULT
# (min ABS_MIN_THR) -> mic pelan otomatis peka, lingkungan normal tak berubah.
QUIET_SNR_MULT = 4.0
ABS_MIN_THR = 120.0
# Frame dengan rms di bawah ini dihitung 'sunyi total' (indikasi klien mengirim
# frame nol karena gerbang noise browser tertutup).
SILENT_FRAME_RMS = 5.0
# Jeda-aman setelah bot selesai mengirim audio, menutup celah sampai klien
# mengirim {"type":"playing",on:true}. Detik.
SPEAK_GUARD_SEC = 0.8


def _log(msg):
    """Log ringkas ke stdout server (fail-soft)."""
    try:
        print("[voicebot.stream] " + msg, flush=True)
    except Exception:
        pass


def _to_int(x):
    return int(float(x))


def _to_float(x):
    return float(x)


def _cfg_num(settings, key, env, default, cast):
    """Ambil angka: config DB dulu (bila terisi), lalu ENV lama, lalu default kode."""
    v = settings.get(key) if settings else None
    if v is not None and str(v).strip() != "":
        try:
            return cast(v)
        except Exception:
            pass
    ev = os.environ.get(env)
    if ev is not None and str(ev).strip() != "":
        try:
            return cast(ev)
        except Exception:
            pass
    return default


def _cfg_bool(settings, key, env, default):
    off = ("0", "false", "False", "no", "NO")
    v = settings.get(key) if settings else None
    if v is not None and str(v).strip() != "":
        return str(v) not in off
    ev = os.environ.get(env)
    if ev is not None and str(ev).strip() != "":
        return str(ev) not in off
    return default


def _stream_tuning(settings):
    """Rangkum seluruh tuning streaming dari config DB (+fallback ENV/default)."""
    s = settings or {}
    aggr = _cfg_num(s, "stream_vad_aggr", "VOICEBOT_STREAM_VAD_AGGR", 3, _to_int)
    aggr = 0 if aggr < 0 else (3 if aggr > 3 else aggr)
    duck_gain = _cfg_num(s, "stream_duck_gain", "VOICEBOT_STREAM_DUCK_GAIN", 0.2, _to_float)
    duck_gain = 0.0 if duck_gain < 0 else (1.0 if duck_gain > 1 else duck_gain)
    onset_frames = _cfg_num(s, "stream_onset_frames", "VOICEBOT_STREAM_ONSET_FRAMES", 3, _to_int)
    onset_frames = 1 if onset_frames < 1 else onset_frames
    vratio = _cfg_num(s, "stream_voiced_ratio_min", "VOICEBOT_STREAM_VOICED_RATIO_MIN", 0.35, _to_float)
    vratio = 0.0 if vratio < 0 else (1.0 if vratio > 1 else vratio)
    snr = _cfg_num(s, "stream_snr_ratio", "VOICEBOT_STREAM_SNR_RATIO", 1.8, _to_float)
    snr = 0.0 if snr < 0 else snr
    return {
        "silence_ms": _cfg_num(s, "stream_silence_ms", "VOICEBOT_STREAM_SILENCE_MS", 700, _to_int),
        "min_speech_ms": _cfg_num(s, "stream_min_speech_ms", "VOICEBOT_STREAM_MIN_SPEECH_MS", 350, _to_int),
        "preroll_ms": _cfg_num(s, "stream_preroll_ms", "VOICEBOT_STREAM_PREROLL_MS", 300, _to_int),
        "rms": _cfg_num(s, "stream_rms", "VOICEBOT_STREAM_RMS", 600, _to_int),
        "vad_aggr": aggr,
        "bargein": _cfg_bool(s, "stream_bargein", "VOICEBOT_STREAM_BARGEIN", True),
        "bargein_min_ms": _cfg_num(s, "stream_bargein_min_ms", "VOICEBOT_STREAM_BARGEIN_MIN_MS", 500, _to_int),
        "speaking_rms": _cfg_num(s, "stream_speaking_rms", "VOICEBOT_STREAM_SPEAKING_RMS", 900, _to_int),
        "gate_rms": _cfg_num(s, "stream_gate_rms", "VOICEBOT_STREAM_GATE_RMS", 0.012, _to_float),
        "gate_hangover_ms": _cfg_num(s, "stream_gate_hangover_ms", "VOICEBOT_STREAM_GATE_HANGOVER_MS", 600, _to_int),
        "ducking": _cfg_bool(s, "stream_ducking", "VOICEBOT_STREAM_DUCKING", True),
        "duck_gain": duck_gain,
        "noise_adapt": _cfg_bool(s, "stream_noise_adapt", "VOICEBOT_STREAM_NOISE_ADAPT", True),
        "snr_ratio": snr,
        "noise_floor_init": _cfg_num(s, "stream_noise_floor_init", "VOICEBOT_STREAM_NOISE_FLOOR_INIT", 150, _to_int),
        "onset_frames": onset_frames,
        "voiced_ratio_min": vratio,
        "autocalibrate": _cfg_bool(s, "stream_autocalibrate", "VOICEBOT_STREAM_AUTOCALIBRATE", True),
        "calib_ms": _cfg_num(s, "stream_calib_ms", "VOICEBOT_STREAM_CALIB_MS", 1200, _to_int),
        "mic_hangover_ms": _cfg_num(s, "stream_mic_hangover_ms", "VOICEBOT_STREAM_MIC_HANGOVER_MS", 250, _to_int),
        "idle_enabled": _cfg_bool(s, "stream_idle_enabled", "VOICEBOT_STREAM_IDLE_ENABLED", True),
        "idle_prompt_ms": _cfg_num(s, "stream_idle_prompt_ms", "VOICEBOT_STREAM_IDLE_PROMPT_MS", 8000, _to_int),
        "idle_end_ms": _cfg_num(s, "stream_idle_end_ms", "VOICEBOT_STREAM_IDLE_END_MS", 10000, _to_int),
        "debug": _cfg_bool(s, "stream_debug", "VOICEBOT_STREAM_DEBUG", True),
    }


# --------------------------------------------------------------------- VAD
_VAD = None
_VAD_AGGR = None
_VAD_FAILED = False


def _get_webrtc_vad(aggr):
    """webrtcvad ter-cache; dibuat ulang bila agresivitas berubah dari config."""
    global _VAD, _VAD_AGGR, _VAD_FAILED
    if _VAD_FAILED:
        return None
    aggr = 0 if aggr < 0 else (3 if aggr > 3 else aggr)
    if _VAD is not None and _VAD_AGGR == aggr:
        return _VAD
    try:
        import webrtcvad  # type: ignore
        _VAD = webrtcvad.Vad(aggr)
        _VAD_AGGR = aggr
    except Exception:
        _VAD = None
        _VAD_FAILED = True
    return _VAD


def _rms(frame: bytes) -> float:
    a = array.array("h")
    try:
        if len(frame) % 2:
            frame = frame[:-1]
        a.frombytes(frame)
    except Exception:
        return 0.0
    if not a:
        return 0.0
    total = 0
    for x in a:
        total += x * x
    return (total / len(a)) ** 0.5


def _vad_verdict(frame: bytes, aggr: int):
    """Kembalikan True/False dari webrtcvad, atau None bila tak tersedia."""
    vad = _get_webrtc_vad(aggr)
    if vad is not None and len(frame) == FRAME_BYTES:
        try:
            return bool(vad.is_speech(frame, SAMPLE_RATE))
        except Exception:
            return None
    return None


def _pcm16_to_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    return buf.getvalue()


class Endpointer:
    """VAD sederhana: deteksi awal bicara + akhiri ucapan setelah hening.

    Anti-noise adaptif (#6, diperbaiki): lantai noise adaptif DIBATASI dan hanya
    belajar dari frame yang webrtcvad pastikan bukan-bicara -> ucapan asli tak
    pernah menaikkan lantai (anti 'budeg'). + frame onset berurutan + rasio frame
    bersuara utk membuang noise/bunyi sesaat/dengungan sebelum STT.

    Anti-budeg #5a: gerbang trigger/endpointing tetap KETAT (vad AND energi),
    tapi rasio-bersuara dihitung LONGGAR (vad OR energi) supaya frame lirih di
    ujung kata / konsonan tak bersuara tetap terhitung sebagai bicara dan
    ucapan asli tidak dibuang sebagai 'noise dominan'.

    Anti-budeg #7: saat MENDENGAR (bukan saat bot bicara), ambang energi juga
    dibatasi dari ATAS oleh lantai_noise*QUIET_SNR_MULT (min ABS_MIN_THR) supaya
    mic pelan (level ucapan < stream_rms) tetap bisa men-trigger. webrtcvad
    (AND) tetap menjaga dari noise. Diagnosa #6b: statistik per-frame dikumpulkan
    (pop_stats) untuk telemetri log ~1x/detik.
    """

    def __init__(self, tuning):
        self.silence_ms = tuning["silence_ms"]
        self.min_speech_ms = tuning["min_speech_ms"]
        self.preroll_ms = tuning["preroll_ms"]
        self.vad_aggr = tuning["vad_aggr"]
        self.rms_default = tuning["rms"]
        self.noise_adapt = tuning["noise_adapt"]
        self.snr_ratio = tuning["snr_ratio"]
        self.noise_floor = float(tuning["noise_floor_init"])
        self._floor_init = float(tuning["noise_floor_init"])
        self.onset_frames = tuning["onset_frames"]
        self.voiced_ratio_min = tuning["voiced_ratio_min"]
        self.debug = bool(tuning.get("debug", True))
        self.stats = self._new_stats()
        self._buf = bytearray()
        self._preroll = bytearray()
        self._speech = bytearray()
        self._triggered = False
        self._silence_run = 0
        self._speech_ms = 0
        self._onset_run = 0
        self._voiced_ms = 0

    @staticmethod
    def _new_stats():
        return {"frames": 0, "rms_sum": 0.0, "rms_max": 0.0, "silent": 0,
                "vad_pos": 0, "above_thr": 0, "strict": 0, "thr_last": 0.0}

    def pop_stats(self):
        """Ambil & reset statistik per-frame (untuk telemetri diagnosa #6b)."""
        st = self.stats
        self.stats = self._new_stats()
        return st

    def reset(self):
        # noise_floor SENGAJA tidak di-reset (dipelajari lintas ucapan).
        self._buf = bytearray()
        self._preroll = bytearray()
        self._speech = bytearray()
        self._triggered = False
        self._silence_run = 0
        self._speech_ms = 0
        self._onset_run = 0
        self._voiced_ms = 0

    @property
    def triggered(self):
        return self._triggered

    @property
    def active_speech_ms(self):
        if not self._triggered:
            return 0
        v = self._speech_ms - self._silence_run
        return v if v > 0 else 0

    def _eff_threshold(self, base_thr, listening=True):
        """Ambang energi efektif: lantai adaptif DIBATASI (anti-budeg #6) dan,
        saat mendengar, DIBATASI DARI ATAS utk mic pelan (anti-budeg #7)."""
        thr = base_thr
        if self.noise_adapt and self.snr_ratio > 0:
            thr = max(base_thr, self.noise_floor * self.snr_ratio)
            thr = min(thr, base_thr * NOISE_FLOOR_CAP_MULT)
        if listening:
            quiet_thr = max(self.noise_floor * QUIET_SNR_MULT, ABS_MIN_THR)
            if quiet_thr < thr:
                thr = quiet_thr
        return thr

    def listen_threshold(self):
        """Ambang efektif saat mendengar (untuk log diagnosa)."""
        return self._eff_threshold(self.rms_default, listening=True)

    def _frame_is_speech(self, frame, rms_min):
        """(is_speech, rms, voiced_like).

        is_speech   : gerbang KETAT (vad AND energi) utk trigger/endpointing.
        voiced_like : hitungan LONGGAR (vad OR energi) khusus rasio-bersuara #5a
                      -- frame lirih tetap dihitung bicara (anti-budeg).
        base_thr = rms_min (penjaga gema saat bot bicara) atau rms_default.
        """
        rms = _rms(frame)
        vad_pos = _vad_verdict(frame, self.vad_aggr)
        listening = rms_min is None
        base_thr = rms_min if rms_min is not None else self.rms_default
        thr = self._eff_threshold(base_thr, listening=listening)
        if vad_pos is not None:
            is_sp = vad_pos and rms >= thr
            voiced_like = vad_pos or rms >= thr
        else:
            is_sp = rms >= thr
            voiced_like = is_sp
        # --- statistik diagnosa #6b
        st = self.stats
        st["frames"] += 1
        st["rms_sum"] += rms
        if rms > st["rms_max"]:
            st["rms_max"] = rms
        if rms < SILENT_FRAME_RMS:
            st["silent"] += 1
        if vad_pos:
            st["vad_pos"] += 1
        if rms >= thr:
            st["above_thr"] += 1
        if is_sp:
            st["strict"] += 1
        st["thr_last"] = thr
        # Perbarui lantai noise HANYA dari non-bicara terpastikan (webrtcvad False),
        # dan hanya saat bot TIDAK bicara. Dibatasi supaya tak pernah 'runaway'.
        if self.noise_adapt and rms_min is None:
            non_speech = (vad_pos is False) if vad_pos is not None else (not is_sp)
            if non_speech:
                self.noise_floor = 0.97 * self.noise_floor + 0.03 * rms
                cap = self.rms_default * NOISE_FLOOR_CAP_MULT / (self.snr_ratio or 1.0)
                lo = 0.25 * self._floor_init
                if self.noise_floor > cap:
                    self.noise_floor = cap
                if self.noise_floor < lo:
                    self.noise_floor = lo
        return is_sp, rms, voiced_like

    def _voiced_ok(self, voiced_ms, spoke_ms):
        """(ok, ratio). ratio memakai hitungan longgar voiced_like (#5a)."""
        if not self.voiced_ratio_min or self.voiced_ratio_min <= 0:
            return True, 1.0
        if spoke_ms <= 0:
            return False, 0.0
        ratio = voiced_ms / float(spoke_ms)
        if ratio > 1.0:
            ratio = 1.0
        ok = ratio >= self.voiced_ratio_min
        if not ok:
            _log("utterance dibuang (#6): rasio bersuara %.2f < %.2f (noise dominan)."
                 % (ratio, self.voiced_ratio_min))
        return ok, ratio

    def add(self, data: bytes, rms_min=None):
        """Proses byte masuk; kembalikan list event:
        ("speech_start",) / ("utterance", wav) / ("discard", ratio)."""
        events = []
        self._buf.extend(data)
        preroll_max = max(FRAME_BYTES, int(self.preroll_ms / FRAME_MS) * FRAME_BYTES)
        while len(self._buf) >= FRAME_BYTES:
            frame = bytes(self._buf[:FRAME_BYTES])
            del self._buf[:FRAME_BYTES]
            speech, _r, voiced_like = self._frame_is_speech(frame, rms_min)
            if not self._triggered:
                self._preroll.extend(frame)
                if len(self._preroll) > preroll_max:
                    del self._preroll[:len(self._preroll) - preroll_max]
                if speech:
                    self._onset_run += 1
                else:
                    self._onset_run = 0
                if self._onset_run >= self.onset_frames:
                    self._triggered = True
                    self._speech = bytearray(self._preroll)
                    self._preroll = bytearray()
                    self._silence_run = 0
                    self._speech_ms = self._onset_run * FRAME_MS
                    self._voiced_ms = self._onset_run * FRAME_MS
                    self._onset_run = 0
                    if self.debug:
                        _log("trigger bicara: %d frame onset berturut (frame terakhir rms~%.0f, thr~%.0f)."
                             % (self.onset_frames, _r, self.stats["thr_last"]))
                    events.append(("speech_start",))
            else:
                self._speech.extend(frame)
                self._speech_ms += FRAME_MS
                if voiced_like:
                    self._voiced_ms += FRAME_MS
                if speech:
                    self._silence_run = 0
                else:
                    self._silence_run += FRAME_MS
                    if self._silence_run >= self.silence_ms:
                        pcm = bytes(self._speech)
                        spoke = self._speech_ms - self._silence_run
                        voiced = self._voiced_ms
                        self.reset()
                        if spoke >= self.min_speech_ms:
                            ok, ratio = self._voiced_ok(voiced, spoke)
                            if ok:
                                _log("utterance SELESAI: bicara %dms (bersuara %dms, rasio %.2f >= %.2f) -> kirim ke STT."
                                     % (spoke, voiced, ratio, self.voiced_ratio_min))
                                events.append(("utterance", _pcm16_to_wav(pcm)))
                            else:
                                events.append(("discard", ratio))
                        else:
                            # terlalu pendek: beri tahu (jangan senyap, #5b)
                            _log("utterance terlalu PENDEK: %dms < %dms -> dibuang (blip/klik)."
                                 % (spoke, self.min_speech_ms))
                            events.append(("discard", 0.0))
        return events

    def flush(self):
        wav = None
        if self._triggered:
            spoke = self._speech_ms - self._silence_run
            ok, _ratio = self._voiced_ok(self._voiced_ms, spoke)
            if spoke >= self.min_speech_ms and ok:
                wav = _pcm16_to_wav(bytes(self._speech))
        self.reset()
        return wav


async def handle(websocket: WebSocket):
    await websocket.accept()
    loop = asyncio.get_event_loop()

    try:
        settings = await loop.run_in_executor(None, cfg.get_settings)
    except Exception:
        settings = {}
    tuning = _stream_tuning(settings)

    try:
        sess = await loop.run_in_executor(None, vb_engine.create_session)
    except Exception as e:  # noqa: BLE001
        try:
            await websocket.send_text(json.dumps({"type": "error", "error": str(e)}))
            await websocket.close()
        except Exception:
            pass
        return

    session_id = sess.get("session_id")
    greeting = sess.get("greeting") or sess.get("salam_pembuka") or ""

    bargein_on = tuning["bargein"]
    ducking = tuning["ducking"]
    speaking_rms = tuning["speaking_rms"]
    bargein_min_ms = tuning["bargein_min_ms"]
    debug = tuning["debug"]

    idle_enabled = tuning["idle_enabled"]
    idle_prompt_ms = tuning["idle_prompt_ms"]
    idle_end_ms = tuning["idle_end_ms"]
    idle_prompt_text = vb_dialog.idle_prompt_text(settings) if idle_enabled else ""
    idle_end_text = vb_dialog.idle_end_text(settings) if idle_enabled else ""

    _log("kode stream versi: %s | debug=%s" % (STREAM_VERSION, debug))
    _log("sesi %s dibuka | bargein=%s ducking=%s speaking_rms=%d bargein_min_ms=%d vad_aggr=%d rms=%d | idle=%s | noise_adapt=%s snr=%.2f floor0=%d onset=%d vratio=%.2f | autocal=%s calib=%dms hang=%dms"
         % (session_id, bargein_on, ducking, speaking_rms, bargein_min_ms,
            tuning["vad_aggr"], tuning["rms"], idle_enabled,
            tuning["noise_adapt"], tuning["snr_ratio"], tuning["noise_floor_init"],
            tuning["onset_frames"], tuning["voiced_ratio_min"],
            tuning["autocalibrate"], tuning["calib_ms"], tuning["mic_hangover_ms"]))

    try:
        await websocket.send_text(json.dumps({
            "type": "ready", "session_id": session_id, "greeting": greeting,
            "sample_rate": SAMPLE_RATE, "bargein": bargein_on,
            "gate_rms": tuning["gate_rms"],
            "gate_hangover_ms": tuning["gate_hangover_ms"],
            "ducking": ducking,
            "duck_gain": tuning["duck_gain"],
            "speaking_rms": speaking_rms,
            "noise_adapt": tuning["noise_adapt"],
            "autocalibrate": tuning["autocalibrate"],
            "calib_ms": tuning["calib_ms"],
            "mic_hangover_ms": tuning["mic_hangover_ms"],
            "idle_watchdog": idle_enabled,
        }))
    except Exception:
        return

    state = {"speaking": False, "interrupt": False, "closed": False,
             "gen": 0, "want_audio": True, "candidate": False, "last_rms": 0.0,
             "processing": False, "last_activity": time.time(),
             "idle_prompted": False, "idle_prompt_at": 0.0,
             "bargein_on": bargein_on, "client_playing": False,
             "speak_guard_until": 0.0, "capture": False,
             "calib_started": False, "calib_done": (not tuning["autocalibrate"]),
             "calib_until": 0.0, "calib_sum": 0.0, "calib_n": 0,
             "diag_next": 0.0}
    ep = Endpointer(tuning)
    queue: asyncio.Queue = asyncio.Queue()

    def _is_talking():
        return (state["speaking"] or state["client_playing"]
                or time.time() < state["speak_guard_until"])

    async def _safe_send_text(payload):
        try:
            await websocket.send_text(json.dumps(payload))
            return True
        except Exception:
            state["closed"] = True
            return False

    async def receiver():
        try:
            while not state["closed"]:
                msg = await websocket.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                data = msg.get("bytes")
                if data:
                    now = time.time()
                    talking = _is_talking()
                    cur_rms = _rms(data) if talking else 0.0
                    if talking:
                        state["last_rms"] = cur_rms
                    # --- half-duplex: barge-in via mic MATI -> abaikan mic saat bot bicara
                    if talking and not state["bargein_on"]:
                        if ep.triggered:
                            ep.reset()
                        state["candidate"] = False
                        state["capture"] = False
                        continue
                    # --- auto-kalibrasi lantai noise (server) di awal sesi saat bot diam
                    if (not talking) and tuning["autocalibrate"] and not state["calib_done"]:
                        if not state["calib_started"]:
                            state["calib_started"] = True
                            state["calib_until"] = now + (tuning["calib_ms"] / 1000.0)
                        if now < state["calib_until"]:
                            state["calib_sum"] += _rms(data)
                            state["calib_n"] += 1
                            continue
                        if state["calib_n"] > 0:
                            amb = state["calib_sum"] / state["calib_n"]
                            ep.noise_floor = max(ep._floor_init * 0.5, amb)
                            _log("auto-kalibrasi: ambient ~%.0f dari %d frame -> noise_floor=%.0f | ambang dengar efektif ~%.0f (dasar rms=%d, anti-budeg #7)."
                                 % (amb, state["calib_n"], ep.noise_floor,
                                    ep.listen_threshold(), ep.rms_default))
                        state["calib_done"] = True
                    rms_min = speaking_rms if talking else None
                    for ev in ep.add(data, rms_min):
                        kind = ev[0]
                        if kind == "speech_start":
                            if not talking:
                                state["last_activity"] = now
                                state["idle_prompted"] = False
                                await _safe_send_text({"type": "speech_start"})
                            # saat talking: interupsi ditangani kandidat/konfirmasi di bawah.
                        elif kind == "utterance":
                            # #5c: ucapan biasa ATAU ucapan penyela barge-in -> proses.
                            if not talking or state["capture"]:
                                state["capture"] = False
                                state["last_activity"] = now
                                state["idle_prompted"] = False
                                _log("utterance masuk ANTREAN proses (%d byte wav, antrean=%d)."
                                     % (len(ev[1]), queue.qsize() + 1))
                                await queue.put(ev[1])
                            else:
                                _log("utterance saat bot bicara DIBUANG (barge-in belum terkonfirmasi) -- kemungkinan gema ATAU ucapan user tertelan jendela 'bot bicara'.")
                        elif kind == "discard":
                            # #5b: jangan senyap -- beri tahu klien supaya UI tak menggantung.
                            if not talking or state["capture"]:
                                state["capture"] = False
                                state["last_activity"] = now
                                await _safe_send_text({
                                    "type": "no_speech",
                                    "reason": "segmen dibuang server (rasio bersuara %.2f)" % ev[1],
                                })
                    # --- telemetri diagnosa #6b: ~1x/detik saat frame mengalir
                    if debug and now >= state["diag_next"]:
                        st = ep.pop_stats()
                        if st["frames"]:
                            avg = st["rms_sum"] / st["frames"]
                            _log("mic~1s: %d frame | rms avg~%.0f max~%.0f | sunyi<%.0f=%d | vad+=%d >=thr=%d ketat=%d (thr~%.0f floor~%.0f) | talking=%s trig=%s aktif=%dms capture=%s"
                                 % (st["frames"], avg, st["rms_max"], SILENT_FRAME_RMS,
                                    st["silent"], st["vad_pos"], st["above_thr"],
                                    st["strict"], st["thr_last"], ep.noise_floor,
                                    talking, ep.triggered, ep.active_speech_ms,
                                    state["capture"]))
                        state["diag_next"] = now + 1.0
                    if talking and state["bargein_on"] and not state["interrupt"]:
                        if ep.triggered and not state["candidate"]:
                            state["candidate"] = True
                            _log("kandidat suara saat bot bicara: rms~%.0f (ambang speaking_rms=%d) -> DUCK."
                                 % (cur_rms, speaking_rms))
                            if ducking:
                                await _safe_send_text({"type": "speech_candidate",
                                                       "rms": round(cur_rms),
                                                       "speaking_rms": speaking_rms})
                        elif state["candidate"] and not ep.triggered:
                            state["candidate"] = False
                            _log("kandidat batal (noise/gema sesaat): rms~%.0f -> pulihkan volume bot."
                                 % cur_rms)
                            if ducking:
                                await _safe_send_text({"type": "speech_cancel"})
                        if ep.triggered and ep.active_speech_ms >= bargein_min_ms:
                            state["interrupt"] = True
                            state["candidate"] = False
                            state["capture"] = True  # #5c: rekam terus ucapan penyela
                            _log("BARGE-IN dikonfirmasi: bicara %dms (>=%dms) berkelanjutan di atas ambang (frame terakhir rms~%.0f) -> POTONG audio bot; ucapan penyela direkam & akan diproses."
                                 % (ep.active_speech_ms, bargein_min_ms, cur_rms))
                            await _safe_send_text({"type": "speech_start",
                                                   "rms": round(cur_rms),
                                                   "speaking_rms": speaking_rms})
                    continue
                txt = msg.get("text")
                if txt:
                    try:
                        ctrl = json.loads(txt)
                    except Exception:
                        ctrl = {}
                    ct = ctrl.get("type")
                    if ct == "hello":
                        if ctrl.get("want_audio") is not None:
                            state["want_audio"] = bool(ctrl.get("want_audio"))
                        if ctrl.get("bargein") is not None:
                            state["bargein_on"] = bool(ctrl.get("bargein"))
                            _log("barge-in via mic (hello) = %s" % state["bargein_on"])
                    elif ct == "bargein":
                        if ctrl.get("on") is not None:
                            state["bargein_on"] = bool(ctrl.get("on"))
                            _log("barge-in via mic di-set %s dari klien." % state["bargein_on"])
                            if not state["bargein_on"]:
                                state["candidate"] = False
                    elif ct == "playing":
                        on = bool(ctrl.get("on"))
                        state["client_playing"] = on
                        state["last_activity"] = time.time()
                        if debug:
                            _log("klien: playing=%s (jendela 'bot bicara' %s)."
                                 % (on, "MULAI" if on else "SELESAI"))
                        if not on:
                            state["speak_guard_until"] = 0.0
                            state["candidate"] = False
                            # #5c: jangan buang buffer bila sedang merekam ucapan penyela.
                            if not state["capture"]:
                                ep.reset()
                    elif ct == "barge_in":
                        _log("barge-in MANUAL (tombol Potong) diterima.")
                        state["interrupt"] = True
                    elif ct == "flush":
                        wav = ep.flush()
                        state["capture"] = False
                        if wav:
                            state["last_activity"] = time.time()
                            state["idle_prompted"] = False
                            await queue.put(wav)
                    elif ct == "bye":
                        break
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            state["closed"] = True
            await queue.put(None)

    async def send_audio(b64, greeting_flag=False):
        try:
            raw = base64.b64decode(b64)
        except Exception:
            return
        begin = {"type": "audio_begin", "mime": "audio/wav"}
        if greeting_flag:
            begin["greeting"] = True
        if not await _safe_send_text(begin):
            return
        state["speaking"] = True
        state["interrupt"] = False
        state["candidate"] = False
        state["capture"] = False
        chunk = 8192
        i = 0
        try:
            while i < len(raw):
                if state["interrupt"] or state["closed"]:
                    break
                try:
                    await websocket.send_bytes(raw[i:i + chunk])
                except Exception:
                    state["closed"] = True
                    break
                i += chunk
                await asyncio.sleep(0)
        finally:
            state["speaking"] = False
            state["candidate"] = False
            # jeda-aman menutup celah sampai klien mengirim playing:true
            state["speak_guard_until"] = time.time() + SPEAK_GUARD_SEC
            state["last_activity"] = time.time()
        if state["interrupt"]:
            # #5c/#5d: user sedang bicara -> buka mic segera (tanpa jeda-aman)
            # dan BERSIHKAN flag supaya jawaban berikutnya tetap dibacakan.
            state["speak_guard_until"] = 0.0
            await _safe_send_text({"type": "interrupted",
                                   "rms": round(state.get("last_rms", 0.0)),
                                   "speaking_rms": speaking_rms})
            state["interrupt"] = False
        elif not state["closed"]:
            await _safe_send_text({"type": "audio_end"})

    async def speak_text(text):
        if not text or state["closed"] or not state["want_audio"]:
            return
        try:
            wav, _terr = await loop.run_in_executor(None, vb_tts.synth, text)
        except Exception:
            wav = None
        if wav and not state["closed"]:
            b64 = base64.b64encode(wav).decode("ascii")
            await send_audio(b64)

    async def greet():
        if not greeting:
            return
        await asyncio.sleep(0.15)
        if state["closed"] or not state["want_audio"]:
            return
        try:
            wav, _terr = await loop.run_in_executor(None, vb_tts.synth, greeting)
        except Exception:
            wav = None
        if wav and not state["closed"]:
            b64 = base64.b64encode(wav).decode("ascii")
            await send_audio(b64, greeting_flag=True)

    async def processor():
        while not state["closed"]:
            wav = await queue.get()
            if wav is None:
                break
            state["processing"] = True
            try:
                state["gen"] += 1
                t_utt = time.time()
                _log("proses #%d: %d byte wav -> STT/NLU..." % (state["gen"], len(wav)))
                await _safe_send_text({"type": "thinking"})
                try:
                    res = await loop.run_in_executor(
                        None, vb_engine.talk, session_id, None, wav, "stream.wav",
                        state["want_audio"], False,
                    )
                except Exception as e:  # noqa: BLE001
                    _log("proses #%d GAGAL: %s" % (state["gen"], e))
                    await _safe_send_text({"type": "error", "error": str(e)})
                    continue
                if state["closed"]:
                    break
                _log("hasil #%d: no_speech=%s transkrip='%s' stt_err=%s intent=%s conf=%s sumber=%s elapsed=%sms"
                     % (state["gen"], bool(res.get("no_speech")),
                        (res.get("transkrip") or "")[:80], res.get("stt_error"),
                        res.get("intent"), res.get("confidence"),
                        res.get("sumber"), res.get("elapsed_ms")))
                if res.get("no_speech") or not (res.get("transkrip") or "").strip():
                    await _safe_send_text({"type": "no_speech",
                                           "reason": "STT tidak mendengar ucapan"})
                    continue
                await _safe_send_text({"type": "transcript",
                                       "text": res.get("transkrip") or ""})
                await _safe_send_text({
                    "type": "answer",
                    "intent": res.get("intent"),
                    "confidence": res.get("confidence"),
                    "sumber": res.get("sumber"),
                    "action": res.get("action"),
                    "jawaban_teks": res.get("jawaban_teks"),
                    "handoff": res.get("handoff"),
                    "stt_error": res.get("stt_error"),
                    "tts_error": res.get("tts_error"),
                    "engine": res.get("engine"),
                    "elapsed_ms": res.get("elapsed_ms"),
                    "timings": res.get("timings"),
                    "server_ms": int((time.time() - t_utt) * 1000),
                })
                b64 = res.get("jawaban_audio_b64")
                if state["want_audio"] and not state["closed"]:
                    if b64:
                        # #5d: jawaban BARU selalu dibacakan. Flag interrupt lama
                        # sudah selesai tugasnya (memotong audio sebelumnya) --
                        # jangan sampai flag basi membuat bot bisu.
                        state["interrupt"] = False
                        await send_audio(b64)
                    else:
                        reason = res.get("tts_error") or "TTS tidak menghasilkan audio"
                        _log("no_audio: %s" % reason)
                        await _safe_send_text({
                            "type": "no_audio",
                            "reason": reason,
                            "tts_error": res.get("tts_error"),
                        })
                if res.get("action") == "end" and not state["closed"]:
                    _log("action=end (salam penutup #4) -> tutup sesi setelah audio.")
                    state["closed"] = True
                    try:
                        await queue.put(None)
                    except Exception:
                        pass
            finally:
                state["processing"] = False
                state["last_activity"] = time.time()
                state["idle_prompted"] = False

    async def watchdog():
        if not idle_enabled:
            return
        while not state["closed"]:
            await asyncio.sleep(0.5)
            if state["closed"]:
                break
            if state["speaking"] or state["processing"] or state["client_playing"]:
                if not state["idle_prompted"]:
                    state["last_activity"] = time.time()
                continue
            now = time.time()
            if not state["idle_prompted"]:
                idle_ms = (now - state["last_activity"]) * 1000.0
                if idle_ms >= idle_prompt_ms:
                    state["idle_prompted"] = True
                    _log("watchdog: diam %.0fms >= %dms -> sapa 'masih terhubung?'."
                         % (idle_ms, idle_prompt_ms))
                    await _safe_send_text({"type": "idle_prompt", "text": idle_prompt_text})
                    await speak_text(idle_prompt_text)
                    state["idle_prompt_at"] = time.time()
            else:
                since = (now - state["idle_prompt_at"]) * 1000.0
                if since >= idle_end_ms:
                    if not state["idle_prompted"]:
                        continue
                    _log("watchdog: masih diam %.0fms >= %dms tanpa respons -> akhiri sesi."
                         % (since, idle_end_ms))
                    await _safe_send_text({"type": "idle_end", "text": idle_end_text})
                    await speak_text(idle_end_text)
                    state["closed"] = True
                    try:
                        await queue.put(None)
                    except Exception:
                        pass
                    break

    recv_task = asyncio.ensure_future(receiver())
    proc_task = asyncio.ensure_future(processor())
    greet_task = asyncio.ensure_future(greet())
    wd_task = asyncio.ensure_future(watchdog())
    try:
        await asyncio.gather(recv_task, proc_task)
    finally:
        state["closed"] = True
        for tsk in (recv_task, proc_task, greet_task, wd_task):
            if not tsk.done():
                tsk.cancel()
        try:
            await loop.run_in_executor(None, vb_engine.end_session, session_id)
        except Exception:
            pass
        try:
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.close()
        except Exception:
            pass
