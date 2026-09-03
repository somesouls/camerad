# -*- coding: utf-8 -*-
"""voicebot/stream.py -- Mode B: percakapan suara real-time (WebSocket) + barge-in.

Endpoint: WS /api/voicebot/stream

Klien -> server:
  * biner  : frame audio PCM16 mono little-endian, sample rate 16 kHz.
  * teks (JSON):
      {"type":"hello","want_audio":true}   -> opsional saat mulai
      {"type":"barge_in"}                    -> paksa hentikan audio bot
      {"type":"flush"}                       -> paksa akhiri ucapan sekarang
      {"type":"bye"}                         -> tutup sesi

Server -> klien:
  * teks (JSON):
      {"type":"ready",...}
      {"type":"speech_start","rms":..,"speaking_rms":..}
      {"type":"speech_candidate","rms":..,"speaking_rms":..} -> (saat bot bicara) kandidat suara; klien MEN-DUCK volume bot
      {"type":"speech_cancel"}               -> kandidat ternyata noise/sesaat; klien kembalikan volume bot
      {"type":"thinking"}
      {"type":"no_speech"}
      {"type":"transcript","text":..}
      {"type":"answer",...}
      {"type":"idle_prompt","text":..}      -> penjaga diam (#3): bot menyapa 'masih terhubung?'
      {"type":"idle_end","text":..}         -> penjaga diam (#3): sesi diakhiri karena tak ada respons
      {"type":"audio_begin","mime":"audio/wav","greeting":true?}
      {"type":"audio_end"}
      {"type":"no_audio","reason":..,"tts_error":..}
      {"type":"interrupted","rms":..,"speaking_rms":..} -> audio bot dipotong (barge-in)
      {"type":"error","error":..}
  * biner  : potongan byte WAV jawaban (antara audio_begin & audio_end).

DIAGNOSTIK BARGE-IN (penting): setiap keputusan deteksi saat bot bicara dicatat
ke log server ([voicebot.stream] ...) lengkap dengan level energi terukur (rms)
vs ambang speaking_rms, supaya bisa ketahuan apakah 'suara diam' disebabkan gema
loudspeaker yang salah dianggap bicara. Nilai rms juga dikirim ke klien pada
pesan speech_candidate / speech_start / interrupted agar bisa ditampilkan di UI.

PENJAGA GEMA (perbaikan): saat bot sedang bicara, sebuah frame dianggap 'bicara'
HANYA bila webrtcvad bilang bicara DAN energi (rms) >= speaking_rms. Sebelumnya,
bila webrtcvad terpasang, ambang energi diabaikan sehingga gema suara bot sendiri
dari speaker terdeteksi sebagai bicara -> barge-in palsu -> audio 'dipotong'.

NOISE vs SPEECH / ANTI-NOISE ADAPTIF (#6): tiga lapis membedakan SUARA ASLI dari
NOISE lingkungan supaya bot tidak menjawab noise/bunyi sesaat/dengungan:
  1) Lantai noise adaptif (stream_noise_adapt): Endpointer memantau energi ambient
     saat senyap (EMA) lalu ambang deteksi efektif = max(ambang energi biasa,
     noise_floor * snr_ratio) -> naik sendiri di tempat berisik, hanya mengetatkan.
  2) Frame onset (stream_onset_frames): butuh N frame bersuara BERURUTAN (30ms/frame)
     sebelum memicu awal bicara -> buang klik/pop/ketikan sesaat.
  3) Rasio frame bersuara (stream_voiced_ratio_min): ucapan hanya diterima bila
     minimal sekian bagian frame-nya benar-benar bersuara -> buang segmen yang
     didominasi noise. Kombinasi ini melengkapi penjaga gema di atas (STT juga
     tetap punya guard reply_on_empty=False + halusinasi #4 sebagai lapis akhir).

PENJAGA DIAM / SILENCE WATCHDOG (#3): sebuah task terpisah memantau berapa lama
penelepon diam. Bila diam >= stream_idle_prompt_ms (default 8 dtk) DAN bot tidak
sedang bicara/memproses, bot menyapa 'masih terhubung?' (stream_idle_prompt_text).
Bila diam berlanjut >= stream_idle_end_ms lagi (default 10 dtk) tanpa respons,
sesi diakhiri otomatis (bot membaca stream_idle_end_text lalu koneksi ditutup).
Timer diam di-reset saat penelepon bicara atau tiap kali bot selesai bicara.

SALAM PENUTUP / CLOSING (#4): bila giliran menghasilkan action='end' (penelepon
mengucapkan 'selesai' ATAU pemicu penutup lunak seperti 'terima kasih' yang
lolos guard di engine), bot membacakan salam penutup APA ADANYA lalu koneksi
ditutup otomatis ('langsung tutup') setelah audio selesai dikirim.

Saat sesi dibuka, bila dialog manager aktif, server mengirim 'ready' berisi teks
salam pembuka LALU langsung mengalirkan AUDIO salam itu (disintesis TTS voicebot)
sebagai audio_begin -> byte WAV -> audio_end dengan flag greeting=true.

KEANDALAN SUARA: setiap jawaban yang PUNYA teks harus punya keluaran audio ATAU
pesan 'no_audio' berisi alasannya -- klien tidak boleh menggantung di 'menunggu'.

STT+NLU+RAG+TTS memakai vb_engine.talk() yang sama dengan Mode A (dengan
reply_on_empty=False). Seluruh konfigurasi berlaku identik. Semua proses lokal.

SEMUA TUNING DI BAWAH DAPAT DIATUR DARI UI (halaman /voicebot, panel \"Streaming
(Mode B) & barge-in\") -> disimpan di vb_settings. Kunci config -> (ENV lama, default):
  stream_silence_ms      (VOICEBOT_STREAM_SILENCE_MS, 700)
  stream_min_speech_ms   (VOICEBOT_STREAM_MIN_SPEECH_MS, 350)
  stream_preroll_ms      (VOICEBOT_STREAM_PREROLL_MS, 300)
  stream_rms             (VOICEBOT_STREAM_RMS, 600)
  stream_vad_aggr        (VOICEBOT_STREAM_VAD_AGGR, 3)
  stream_bargein         (VOICEBOT_STREAM_BARGEIN, 1)
  stream_bargein_min_ms  (VOICEBOT_STREAM_BARGEIN_MIN_MS, 500)
  stream_speaking_rms    (VOICEBOT_STREAM_SPEAKING_RMS, 900)
  stream_gate_rms        (VOICEBOT_STREAM_GATE_RMS, 0.012)     [gerbang noise browser]
  stream_gate_hangover_ms(VOICEBOT_STREAM_GATE_HANGOVER_MS, 600)
  stream_ducking         (VOICEBOT_STREAM_DUCKING, 1)
  stream_duck_gain       (VOICEBOT_STREAM_DUCK_GAIN, 0.2)
  stream_noise_adapt     (VOICEBOT_STREAM_NOISE_ADAPT, 1)      [anti-noise adaptif #6]
  stream_snr_ratio       (VOICEBOT_STREAM_SNR_RATIO, 1.8)
  stream_noise_floor_init(VOICEBOT_STREAM_NOISE_FLOOR_INIT, 150)
  stream_onset_frames    (VOICEBOT_STREAM_ONSET_FRAMES, 3)
  stream_voiced_ratio_min(VOICEBOT_STREAM_VOICED_RATIO_MIN, 0.35)
  stream_idle_enabled    (VOICEBOT_STREAM_IDLE_ENABLED, 1)     [penjaga diam #3]
  stream_idle_prompt_ms  (VOICEBOT_STREAM_IDLE_PROMPT_MS, 8000)
  stream_idle_end_ms     (VOICEBOT_STREAM_IDLE_END_MS, 10000)
  stream_idle_prompt_text / stream_idle_end_text (teks; via voicebot.dialog)
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


SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_BYTES = int(SAMPLE_RATE * FRAME_MS / 1000) * 2  # 960 byte / frame 30ms


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
    # noise-vs-speech / anti-noise adaptif (#6) \u2014 dengan clamp aman
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
        # noise-vs-speech / anti-noise adaptif (#6)
        "noise_adapt": _cfg_bool(s, "stream_noise_adapt", "VOICEBOT_STREAM_NOISE_ADAPT", True),
        "snr_ratio": snr,
        "noise_floor_init": _cfg_num(s, "stream_noise_floor_init", "VOICEBOT_STREAM_NOISE_FLOOR_INIT", 150, _to_int),
        "onset_frames": onset_frames,
        "voiced_ratio_min": vratio,
        # penjaga diam / silence watchdog (#3)
        "idle_enabled": _cfg_bool(s, "stream_idle_enabled", "VOICEBOT_STREAM_IDLE_ENABLED", True),
        "idle_prompt_ms": _cfg_num(s, "stream_idle_prompt_ms", "VOICEBOT_STREAM_IDLE_PROMPT_MS", 8000, _to_int),
        "idle_end_ms": _cfg_num(s, "stream_idle_end_ms", "VOICEBOT_STREAM_IDLE_END_MS", 10000, _to_int),
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
        # array.frombytes butuh panjang kelipatan 2; potong byte ganjil di ujung
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


def _is_speech(frame: bytes, aggr: int, rms_default: int, rms_min=None) -> bool:
    """True bila frame dianggap bicara (deteksi dasar tanpa lantai adaptif).

    Dipertahankan untuk kompatibilitas / pemakaian sederhana. Endpointer memakai
    _frame_is_speech() yang menambah lantai noise adaptif (#6). PENJAGA GEMA: saat
    bot bicara (rms_min di-set), frame dianggap bicara HANYA bila webrtcvad bilang
    bicara DAN energi (rms) >= rms_min.
    """
    vad = _get_webrtc_vad(aggr)
    rms = _rms(frame)
    if vad is not None and len(frame) == FRAME_BYTES:
        try:
            sp = bool(vad.is_speech(frame, SAMPLE_RATE))
        except Exception:
            sp = None
        if sp is not None:
            if rms_min is not None:
                return sp and rms >= rms_min
            return sp
    thr = rms_min if rms_min is not None else rms_default
    return rms >= thr


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

    Tuning diambil dari dict hasil _stream_tuning (config DB / ENV / default).
    Anti-noise adaptif (#6): lantai noise adaptif + frame onset berurutan + cek
    rasio frame bersuara -> membuang noise/bunyi sesaat/dengungan sebelum STT.
    """

    def __init__(self, tuning):
        self.silence_ms = tuning["silence_ms"]
        self.min_speech_ms = tuning["min_speech_ms"]
        self.preroll_ms = tuning["preroll_ms"]
        self.vad_aggr = tuning["vad_aggr"]
        self.rms_default = tuning["rms"]
        # noise-vs-speech / anti-noise adaptif (#6)
        self.noise_adapt = tuning["noise_adapt"]
        self.snr_ratio = tuning["snr_ratio"]
        self.noise_floor = float(tuning["noise_floor_init"])
        self.onset_frames = tuning["onset_frames"]
        self.voiced_ratio_min = tuning["voiced_ratio_min"]
        self._buf = bytearray()       # byte mentah belum jadi frame utuh
        self._preroll = bytearray()   # ring pra-bicara
        self._speech = bytearray()    # ucapan berjalan
        self._triggered = False
        self._silence_run = 0
        self._speech_ms = 0
        self._onset_run = 0           # frame bersuara berurutan sebelum trigger (#6)
        self._voiced_ms = 0           # akumulasi ms bersuara dalam ucapan (#6)

    def reset(self):
        # catatan: noise_floor SENGAJA tidak di-reset (dipelajari lintas ucapan).
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
        """Durasi bicara berkelanjutan saat ini (ms), dikurangi hening di ujung."""
        if not self._triggered:
            return 0
        v = self._speech_ms - self._silence_run
        return v if v > 0 else 0

    def _frame_is_speech(self, frame, rms_min):
        """Kembalikan (is_speech, rms) untuk satu frame, dengan lantai noise adaptif.

        Anti-noise adaptif (#6): ambang energi efektif = max(ambang biasa,
        noise_floor * snr_ratio). noise_floor dipantau otomatis (EMA) dari frame
        NON-bicara saat bot TIDAK bicara -> ambang naik sendiri di lingkungan
        berisik. Ambang hanya MENGETATKAN (tak pernah lebih longgar dari
        rms_default / rms_min penjaga gema).
        """
        rms = _rms(frame)
        vad = _get_webrtc_vad(self.vad_aggr)
        vad_pos = None
        if vad is not None and len(frame) == FRAME_BYTES:
            try:
                vad_pos = bool(vad.is_speech(frame, SAMPLE_RATE))
            except Exception:
                vad_pos = None
        base_thr = rms_min if rms_min is not None else self.rms_default
        thr = base_thr
        if self.noise_adapt and self.snr_ratio > 0:
            thr = max(base_thr, self.noise_floor * self.snr_ratio)
        if vad_pos is not None:
            is_sp = vad_pos and rms >= thr
        else:
            is_sp = rms >= thr
        # perbarui lantai noise dari frame ambient (bukan bicara), hanya saat bot diam
        if self.noise_adapt and rms_min is None and not is_sp:
            self.noise_floor = 0.95 * self.noise_floor + 0.05 * rms
        return is_sp, rms

    def _voiced_ok(self, voiced_ms, spoke_ms):
        """True bila rasio frame bersuara memenuhi voiced_ratio_min (#6)."""
        if not self.voiced_ratio_min or self.voiced_ratio_min <= 0:
            return True
        if spoke_ms <= 0:
            return False
        ratio = voiced_ms / float(spoke_ms)
        ok = ratio >= self.voiced_ratio_min
        if not ok:
            _log("utterance dibuang (#6): rasio bersuara %.2f < %.2f (noise dominan)."
                 % (ratio, self.voiced_ratio_min))
        return ok

    def add(self, data: bytes, rms_min=None):
        """Proses byte masuk; kembalikan list event.

        Event: ("speech_start",) atau ("utterance", wav_bytes).
        rms_min menaikkan ambang energi (fallback non-webrtcvad) saat bot bicara.
        Anti-noise adaptif (#6): butuh onset_frames frame bersuara BERURUTAN untuk
        memicu awal bicara (buang klik/pop sesaat), dan ucapan hanya diterima bila
        rasio frame bersuara >= voiced_ratio_min (buang segmen didominasi noise).
        """
        events = []
        self._buf.extend(data)
        preroll_max = max(FRAME_BYTES, int(self.preroll_ms / FRAME_MS) * FRAME_BYTES)
        while len(self._buf) >= FRAME_BYTES:
            frame = bytes(self._buf[:FRAME_BYTES])
            del self._buf[:FRAME_BYTES]
            speech, _r = self._frame_is_speech(frame, rms_min)
            if not self._triggered:
                self._preroll.extend(frame)
                if len(self._preroll) > preroll_max:
                    del self._preroll[:len(self._preroll) - preroll_max]
                if speech:
                    self._onset_run += 1
                else:
                    self._onset_run = 0
                if self._onset_run >= self.onset_frames:
                    # onset dikonfirmasi -> mulai rekam ucapan. preroll sudah memuat
                    # frame-frame onset; speech_ms dihitung dari onset (bukan preroll).
                    self._triggered = True
                    self._speech = bytearray(self._preroll)
                    self._preroll = bytearray()
                    self._silence_run = 0
                    self._speech_ms = self._onset_run * FRAME_MS
                    self._voiced_ms = self._onset_run * FRAME_MS
                    self._onset_run = 0
                    events.append(("speech_start",))
            else:
                self._speech.extend(frame)
                self._speech_ms += FRAME_MS
                if speech:
                    self._silence_run = 0
                    self._voiced_ms += FRAME_MS
                else:
                    self._silence_run += FRAME_MS
                    if self._silence_run >= self.silence_ms:
                        pcm = bytes(self._speech)
                        spoke = self._speech_ms - self._silence_run
                        voiced = self._voiced_ms
                        self.reset()
                        if spoke >= self.min_speech_ms and self._voiced_ok(voiced, spoke):
                            events.append(("utterance", _pcm16_to_wav(pcm)))
        return events

    def flush(self):
        wav = None
        if self._triggered:
            spoke = self._speech_ms - self._silence_run
            if spoke >= self.min_speech_ms and self._voiced_ok(self._voiced_ms, spoke):
                wav = _pcm16_to_wav(bytes(self._speech))
        self.reset()
        return wav


async def handle(websocket: WebSocket):
    await websocket.accept()
    loop = asyncio.get_event_loop()

    # Muat tuning streaming dari config DB (dapat diatur di UI). Sekali per sesi.
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

    # penjaga diam / silence watchdog (#3)
    idle_enabled = tuning["idle_enabled"]
    idle_prompt_ms = tuning["idle_prompt_ms"]
    idle_end_ms = tuning["idle_end_ms"]
    idle_prompt_text = vb_dialog.idle_prompt_text(settings) if idle_enabled else ""
    idle_end_text = vb_dialog.idle_end_text(settings) if idle_enabled else ""

    _log("sesi %s dibuka | bargein=%s ducking=%s speaking_rms=%d bargein_min_ms=%d vad_aggr=%d rms=%d | idle=%s prompt=%dms end=%dms | noise_adapt=%s snr=%.2f floor0=%d onset=%d vratio=%.2f"
         % (session_id, bargein_on, ducking, speaking_rms, bargein_min_ms,
            tuning["vad_aggr"], tuning["rms"], idle_enabled, idle_prompt_ms, idle_end_ms,
            tuning["noise_adapt"], tuning["snr_ratio"], tuning["noise_floor_init"],
            tuning["onset_frames"], tuning["voiced_ratio_min"]))

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
            "idle_watchdog": idle_enabled,
        }))
    except Exception:
        return

    state = {"speaking": False, "interrupt": False, "closed": False,
             "gen": 0, "want_audio": True, "candidate": False, "last_rms": 0.0,
             "processing": False, "last_activity": time.time(),
             "idle_prompted": False, "idle_prompt_at": 0.0}
    ep = Endpointer(tuning)
    queue: asyncio.Queue = asyncio.Queue()

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
                    # saat bot bicara, naikkan ambang energi agar gema tak dianggap bicara
                    rms_min = speaking_rms if state["speaking"] else None
                    cur_rms = _rms(data) if state["speaking"] else 0.0
                    if state["speaking"]:
                        state["last_rms"] = cur_rms
                    for ev in ep.add(data, rms_min):
                        if ev[0] == "speech_start":
                            # penelepon bersuara -> reset penjaga diam (#3)
                            state["last_activity"] = time.time()
                            state["idle_prompted"] = False
                            if not state["speaking"]:
                                await _safe_send_text({"type": "speech_start"})
                            # saat bot bicara: kandidat/konfirmasi ditangani di bawah
                        elif ev[0] == "utterance":
                            state["last_activity"] = time.time()
                            state["idle_prompted"] = False
                            await queue.put(ev[1])
                    # barge-in tahan-gema + ducking, hanya saat bot bicara
                    if state["speaking"] and bargein_on and not state["interrupt"]:
                        if ep.triggered and not state["candidate"]:
                            # kandidat suara muncul -> minta klien duck volume bot
                            state["candidate"] = True
                            _log("kandidat suara saat bot bicara: rms~%.0f (ambang speaking_rms=%d) -> DUCK volume bot."
                                 % (cur_rms, speaking_rms))
                            if ducking:
                                await _safe_send_text({"type": "speech_candidate",
                                                       "rms": round(cur_rms),
                                                       "speaking_rms": speaking_rms})
                        elif state["candidate"] and not ep.triggered:
                            # kandidat hilang tanpa dikonfirmasi (noise) -> unduck
                            state["candidate"] = False
                            _log("kandidat batal (noise/gema sesaat): rms~%.0f -> pulihkan volume bot."
                                 % cur_rms)
                            if ducking:
                                await _safe_send_text({"type": "speech_cancel"})
                        # konfirmasi barge-in: hanya bila bicara BERKELANJUTAN cukup lama
                        if ep.triggered and ep.active_speech_ms >= bargein_min_ms:
                            state["interrupt"] = True
                            state["candidate"] = False
                            _log("BARGE-IN dikonfirmasi: rms~%.0f (>=speaking_rms %d) & bicara %dms (>=%dms) -> POTONG audio bot."
                                 % (cur_rms, speaking_rms, ep.active_speech_ms, bargein_min_ms))
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
                    elif ct == "barge_in":
                        _log("barge-in MANUAL (tombol Potong) diterima.")
                        state["interrupt"] = True
                    elif ct == "flush":
                        wav = ep.flush()
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
                await asyncio.sleep(0)  # beri kesempatan receiver menangkap barge-in
        finally:
            state["speaking"] = False
            state["candidate"] = False
            # bot selesai bicara -> mulai hitung ulang diam penelepon (#3)
            state["last_activity"] = time.time()
        if state["interrupt"]:
            await _safe_send_text({"type": "interrupted",
                                   "rms": round(state.get("last_rms", 0.0)),
                                   "speaking_rms": speaking_rms})
        elif not state["closed"]:
            await _safe_send_text({"type": "audio_end"})

    async def speak_text(text):
        """Sintesis + kirim AUDIO teks arbitrer (dipakai penjaga diam #3)."""
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
        """Sintesis + kirim AUDIO salam pembuka (suara voicebot) saat sesi dibuka."""
        if not greeting:
            return
        await asyncio.sleep(0.15)  # beri kesempatan 'hello' (want_audio) tiba
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
                await _safe_send_text({"type": "thinking"})
                try:
                    res = await loop.run_in_executor(
                        None, vb_engine.talk, session_id, None, wav, "stream.wav",
                        state["want_audio"], False,
                    )
                except Exception as e:  # noqa: BLE001
                    await _safe_send_text({"type": "error", "error": str(e)})
                    continue
                if state["closed"]:
                    break
                # STT tak menangkap ucapan (noise/gema) -> jangan membalas apa pun.
                if res.get("no_speech") or not (res.get("transkrip") or "").strip():
                    await _safe_send_text({"type": "no_speech"})
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
                # KEANDALAN SUARA: setiap jawaban ber-teks harus menghasilkan audio,
                # 'interrupted', atau 'no_audio' (beserta alasannya). JANGAN diam.
                b64 = res.get("jawaban_audio_b64")
                if state["want_audio"] and not state["closed"]:
                    if b64 and not state["interrupt"]:
                        await send_audio(b64)
                    elif b64 and state["interrupt"]:
                        await _safe_send_text({"type": "interrupted",
                                               "rms": round(state.get("last_rms", 0.0)),
                                               "speaking_rms": speaking_rms})
                    elif not b64:
                        reason = res.get("tts_error") or "TTS tidak menghasilkan audio"
                        _log("no_audio: %s" % reason)
                        await _safe_send_text({
                            "type": "no_audio",
                            "reason": reason,
                            "tts_error": res.get("tts_error"),
                        })
                # SALAM PENUTUP (#4): action='end' (perintah 'selesai' ATAU pemicu
                # penutup lunak 'terima kasih' yang lolos guard engine) -> setelah
                # salam penutup dibacakan, tutup sesi otomatis ('langsung tutup').
                if res.get("action") == "end" and not state["closed"]:
                    _log("action=end (salam penutup #4) -> tutup sesi setelah audio.")
                    state["closed"] = True
                    try:
                        await queue.put(None)
                    except Exception:
                        pass
            finally:
                # selesai satu giliran -> reset penjaga diam (#3)
                state["processing"] = False
                state["last_activity"] = time.time()
                state["idle_prompted"] = False

    async def watchdog():
        """Penjaga diam (#3): diam >= idle_prompt_ms -> sapa 'masih terhubung?';
        diam berlanjut >= idle_end_ms lagi tanpa respons -> akhiri sesi.
        Timer hanya berjalan saat bot TIDAK bicara/memproses.
        """
        if not idle_enabled:
            return
        while not state["closed"]:
            await asyncio.sleep(0.5)
            if state["closed"]:
                break
            if state["speaking"] or state["processing"]:
                # bot sibuk -> jangan hitung diam (kecuali sedang menyapa & menunggu respons)
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
                    # mulai hitung jendela akhir SETELAH sapaan selesai dibacakan
                    state["idle_prompt_at"] = time.time()
            else:
                # sudah menyapa; tunggu respons sampai idle_end_ms lagi
                since = (now - state["idle_prompt_at"]) * 1000.0
                if since >= idle_end_ms:
                    if not state["idle_prompted"]:
                        # penelepon keburu merespons -> batalkan penutupan
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
