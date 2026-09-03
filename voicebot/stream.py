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
      {"type":"ready","session_id":..,"greeting":..,"sample_rate":16000,"bargein":true,
              "gate_rms":..,"gate_hangover_ms":..,"ducking":..,"duck_gain":..}
      {"type":"speech_start"}                -> VAD mendeteksi user bicara (atau barge-in dikonfirmasi)
      {"type":"speech_candidate"}            -> (saat bot bicara) kandidat suara terdeteksi; klien MEN-DUCK (kecilkan) volume bot sambil verifikasi
      {"type":"speech_cancel"}               -> kandidat ternyata noise/sesaat; klien kembalikan volume bot ke normal
      {"type":"thinking"}                    -> ucapan selesai, sedang diproses
      {"type":"no_speech"}                   -> STT tak menangkap ucapan; tak ada balasan (noise)
      {"type":"transcript","text":..}
      {"type":"answer","intent":..,"confidence":..,"sumber":..,"action":..,
                       "jawaban_teks":..,"handoff":..,"elapsed_ms":..,
                       "timings":{stt_ms,think_ms,tts_ms,total_ms},"server_ms":..}
      {"type":"audio_begin","mime":"audio/wav","greeting":true?}
      {"type":"audio_end"}
      {"type":"no_audio","reason":..,"tts_error":..} -> jawaban ADA tapi TAK ada audio; alasan disertakan untuk log klien
      {"type":"interrupted"}                 -> audio bot dipotong (barge-in)
      {"type":"error","error":..}
  * biner  : potongan byte WAV jawaban (antara audio_begin & audio_end).

Saat sesi dibuka, bila dialog manager aktif, server mengirim 'ready' berisi teks
salam pembuka LALU langsung mengalirkan AUDIO salam itu (disintesis TTS voicebot)
sebagai audio_begin -> byte WAV -> audio_end dengan flag greeting=true. Jadi klien
(APK/browser) memutar salam dengan SUARA VOICEBOT, bukan TTS bawaan perangkat.

KEANDALAN SUARA: setiap jawaban yang PUNYA teks harus punya keluaran audio ATAU
pesan 'no_audio' berisi alasannya -- klien tidak boleh menggantung di 'menunggu'.
Bila audio sudah jadi tetapi user MEMOTONG (interrupt) sebelum diputar, server
mengirim 'interrupted' (bukan diam). Bila TTS gagal / tak ada audio, server
mengirim 'no_audio' + reason (mis. isi tts_error) supaya bisa didiagnosis di UI.

STT+NLU+RAG+TTS memakai vb_engine.talk() yang sama dengan Mode A (dengan
reply_on_empty=False: bila STT tak menangkap ucapan, server TIDAK membalas apa pun
-> mengirim {"type":"no_speech"} saja, supaya noise/gema tidak memicu 'maaf'
berulang atau handoff palsu). Seluruh konfigurasi (ambang, dialog manager,
pelafalan, mesin TTS, penyingkat jawaban) berlaku identik. Semua proses tetap lokal.

Barge-in tanpa headset (loudspeaker): kombinasi (a) AEC/NS/AGC + gerbang noise di
sisi klien untuk membuang gema suara bot & noise steady dari mic, dan (b) 'barge-in
tahan-gema' di server -- saat bot sedang bicara, interupsi HANYA dikonfirmasi bila
terdeteksi bicara BERKELANJUTAN >= bargein_min_ms dan (fallback RMS) energi >=
speaking_rms. Blip/gema sesaat diabaikan. Praktik terbaik ditambahkan: saat ada
KANDIDAT suara (belum dikonfirmasi) selama bot bicara, server mengirim
'speech_candidate' agar klien MEN-DUCK (mengecilkan) volume bot; bila kandidat
berubah jadi noise -> 'speech_cancel' (volume kembali); bila berkelanjutan ->
dikonfirmasi jadi barge-in penuh (audio dihentikan). Onset ucapan tetap direkam
via pre-roll sehingga kata pertama tak hilang.

Endpointing pakai webrtcvad bila terpasang, kalau tidak jatuh ke VAD energi (RMS).

SEMUA TUNING DI BAWAH DAPAT DIATUR DARI UI (halaman /voicebot, panel \"Streaming
(Mode B) & barge-in\") -> disimpan di vb_settings. Bila sebuah nilai dikosongkan,
sistem fallback ke ENV lama lalu default kode. Kunci config -> (ENV lama, default):
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
from voicebot import config_db as cfg


SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_BYTES = int(SAMPLE_RATE * FRAME_MS / 1000) * 2  # 960 byte / frame 30ms


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
    """True bila frame dianggap bicara.

    webrtcvad dipakai bila tersedia (robustnya dari agresivitas VAD + durasi
    berkelanjutan di pemanggil). Bila tidak, fallback ke energi RMS; ambang bisa
    dinaikkan lewat rms_min saat bot sedang bicara agar gema loudspeaker tak
    memicu bicara palsu.
    """
    vad = _get_webrtc_vad(aggr)
    if vad is not None and len(frame) == FRAME_BYTES:
        try:
            return bool(vad.is_speech(frame, SAMPLE_RATE))
        except Exception:
            pass
    thr = rms_min if rms_min is not None else rms_default
    return _rms(frame) >= thr


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
    """

    def __init__(self, tuning):
        self.silence_ms = tuning["silence_ms"]
        self.min_speech_ms = tuning["min_speech_ms"]
        self.preroll_ms = tuning["preroll_ms"]
        self.vad_aggr = tuning["vad_aggr"]
        self.rms_default = tuning["rms"]
        self._buf = bytearray()       # byte mentah belum jadi frame utuh
        self._preroll = bytearray()   # ring pra-bicara
        self._speech = bytearray()    # ucapan berjalan
        self._triggered = False
        self._silence_run = 0
        self._speech_ms = 0

    def reset(self):
        self._speech = bytearray()
        self._triggered = False
        self._silence_run = 0
        self._speech_ms = 0

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

    def add(self, data: bytes, rms_min=None):
        """Proses byte masuk; kembalikan list event.

        Event: ("speech_start",) atau ("utterance", wav_bytes).
        rms_min menaikkan ambang energi (fallback non-webrtcvad) saat bot bicara.
        """
        events = []
        self._buf.extend(data)
        preroll_max = max(FRAME_BYTES, int(self.preroll_ms / FRAME_MS) * FRAME_BYTES)
        while len(self._buf) >= FRAME_BYTES:
            frame = bytes(self._buf[:FRAME_BYTES])
            del self._buf[:FRAME_BYTES]
            speech = _is_speech(frame, self.vad_aggr, self.rms_default, rms_min)
            if not self._triggered:
                self._preroll.extend(frame)
                if len(self._preroll) > preroll_max:
                    del self._preroll[:len(self._preroll) - preroll_max]
                if speech:
                    self._triggered = True
                    self._speech = bytearray(self._preroll)
                    self._speech.extend(frame)
                    self._preroll = bytearray()
                    self._silence_run = 0
                    self._speech_ms = FRAME_MS
                    events.append(("speech_start",))
            else:
                self._speech.extend(frame)
                self._speech_ms += FRAME_MS
                if speech:
                    self._silence_run = 0
                else:
                    self._silence_run += FRAME_MS
                    if self._silence_run >= self.silence_ms:
                        pcm = bytes(self._speech)
                        spoke = self._speech_ms - self._silence_run
                        self.reset()
                        if spoke >= self.min_speech_ms:
                            events.append(("utterance", _pcm16_to_wav(pcm)))
        return events

    def flush(self):
        wav = None
        if self._triggered and (self._speech_ms - self._silence_run) >= self.min_speech_ms:
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

    try:
        await websocket.send_text(json.dumps({
            "type": "ready", "session_id": session_id, "greeting": greeting,
            "sample_rate": SAMPLE_RATE, "bargein": bargein_on,
            "gate_rms": tuning["gate_rms"],
            "gate_hangover_ms": tuning["gate_hangover_ms"],
            "ducking": ducking,
            "duck_gain": tuning["duck_gain"],
        }))
    except Exception:
        return

    state = {"speaking": False, "interrupt": False, "closed": False,
             "gen": 0, "want_audio": True, "candidate": False}
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
                    for ev in ep.add(data, rms_min):
                        if ev[0] == "speech_start":
                            if not state["speaking"]:
                                await _safe_send_text({"type": "speech_start"})
                            # saat bot bicara: kandidat/konfirmasi ditangani di bawah
                        elif ev[0] == "utterance":
                            await queue.put(ev[1])
                    # barge-in tahan-gema + ducking, hanya saat bot bicara
                    if state["speaking"] and bargein_on and not state["interrupt"]:
                        if ep.triggered and not state["candidate"]:
                            # kandidat suara muncul -> minta klien duck volume bot
                            state["candidate"] = True
                            if ducking:
                                await _safe_send_text({"type": "speech_candidate"})
                        elif state["candidate"] and not ep.triggered:
                            # kandidat hilang tanpa dikonfirmasi (noise) -> unduck
                            state["candidate"] = False
                            if ducking:
                                await _safe_send_text({"type": "speech_cancel"})
                        # konfirmasi barge-in: hanya bila bicara BERKELANJUTAN cukup lama
                        if ep.triggered and ep.active_speech_ms >= bargein_min_ms:
                            state["interrupt"] = True
                            state["candidate"] = False
                            await _safe_send_text({"type": "speech_start"})
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
                        state["interrupt"] = True
                    elif ct == "flush":
                        wav = ep.flush()
                        if wav:
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
        if state["interrupt"]:
            await _safe_send_text({"type": "interrupted"})
        elif not state["closed"]:
            await _safe_send_text({"type": "audio_end"})

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
            # Audio jawaban tetap diputar meski ada ucapan lain menyusul di antrean.
            # Hanya barge-in (interrupt) atau sesi tertutup yang membatalkan audio,
            # supaya suara jawaban benar-benar terdengar untuk evaluasi.
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
            # 'interrupted', atau 'no_audio' (beserta alasannya). JANGAN diam --
            # kalau diam, klien menggantung di 'menunggu' seperti bug sebelumnya.
            b64 = res.get("jawaban_audio_b64")
            if state["want_audio"] and not state["closed"]:
                if b64 and not state["interrupt"]:
                    await send_audio(b64)
                elif b64 and state["interrupt"]:
                    # audio sudah jadi tetapi user memotong sebelum diputar
                    await _safe_send_text({"type": "interrupted"})
                elif not b64:
                    reason = res.get("tts_error") or "TTS tidak menghasilkan audio"
                    try:
                        print("[voicebot.stream] no_audio: %s" % reason, flush=True)
                    except Exception:
                        pass
                    await _safe_send_text({
                        "type": "no_audio",
                        "reason": reason,
                        "tts_error": res.get("tts_error"),
                    })

    recv_task = asyncio.ensure_future(receiver())
    proc_task = asyncio.ensure_future(processor())
    greet_task = asyncio.ensure_future(greet())
    try:
        await asyncio.gather(recv_task, proc_task)
    finally:
        state["closed"] = True
        for tsk in (recv_task, proc_task, greet_task):
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
