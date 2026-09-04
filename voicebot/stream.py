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
   b. TELEMETRI MIC ~1x/detik (stream_debug, default MATI sejak kondisi stabil;
      nyalakan saat diagnosa): jumlah frame,
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

7) ANTI TUMPANG TINDIH AUDIO (#8) + TANGKAP UCAPAN PERTAMA (#1) (perbaikan 4 Sep):
   a. Semua pengiriman audio ke klien lewat SATU kunci (audio_lock) sehingga byte
      salam & jawaban tak pernah saling menyisip, DAN sebelum mengirim audio
      jawaban berikutnya server MENUNGGU klien selesai memutar audio sebelumnya
      (sinyal {"type":"playing",on:false}); ada timeout pengaman. Hasilnya jawaban
      tidak lagi tumpang tindih (audio 2 tak diputar sebelum audio 1 habis).
   b. Ucapan PERTAMA penelepon saat salam pembuka masih diputar tidak lagi
      dibuang; direkam lalu diproses, dan jawabannya baru dibacakan setelah salam
      selesai (butuh 'barge-in via mic' aktif). Sesudah giliran pertama, perilaku
      penjaga-gema saat bot bicara kembali ketat seperti biasa.

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
  stream_debug(0; set 1 utk telemetri diagnosa ~1x/detik)
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
STREAM_VERSION = "2026-09-04a (anti tumpang tindih #8 + tangkap ucapan pertama saat salam)"

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
# #8 anti tumpang tindih: batas waktu (detik) menunggu klien selesai memutar audio
# sebelumnya sebelum mengirim audio jawaban berikutnya. Pengaman agar tak
# menggantung selamanya bila klien tak pernah mengirim {"type":"playing",on:false}.
PLAYBACK_WAIT_MAX_SEC = 25.0


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
        "debug": _cfg_bool(s, "stream_debug", "VOICEBOT_STREAM_DEBUG", False),
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
        self.debug = bool(tuning.get("debug", False))
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
                "vad_pos": 0, "above_thr": 0, "strict": 0, "th