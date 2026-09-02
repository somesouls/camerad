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
      {"type":"ready","session_id":..,"greeting":..,"sample_rate":16000,"bargein":true}
      {"type":"speech_start"}                -> VAD mendeteksi user bicara (atau barge-in dikonfirmasi)
      {"type":"thinking"}                    -> ucapan selesai, sedang diproses
      {"type":"transcript","text":..}
      {"type":"answer","intent":..,"confidence":..,"sumber":..,"action":..,
                       "jawaban_teks":..,"handoff":..,"elapsed_ms":..,
                       "timings":{stt_ms,think_ms,tts_ms,total_ms},"server_ms":..}
      {"type":"audio_begin","mime":"audio/wav","greeting":true?}
      {"type":"audio_end"}
      {"type":"interrupted"}                 -> audio bot dipotong (barge-in)
      {"type":"error","error":..}
  * biner  : potongan byte WAV jawaban (antara audio_begin & audio_end).

Saat sesi dibuka, bila dialog manager aktif, server mengirim 'ready' berisi teks
salam pembuka LALU langsung mengalirkan AUDIO salam itu (disintesis TTS voicebot)
sebagai audio_begin -> byte WAV -> audio_end dengan flag greeting=true. Jadi klien
(APK/browser) memutar salam dengan SUARA VOICEBOT, bukan TTS bawaan perangkat.

STT+NLU+RAG+TTS memakai vb_engine.talk() yang sama dengan Mode A, jadi seluruh
konfigurasi (ambang, dialog manager, pelafalan, mesin TTS Piper/MMS, penyingkat
jawaban) berlaku identik. Semua proses tetap lokal.

Barge-in tanpa headset (loudspeaker): kombinasi (a) AEC/NS/AGC di sisi klien
(browser getUserMedia echoCancellation, atau AcousticEchoCanceler + AudioSource
VOICE_COMMUNICATION di Android) untuk membuang gema suara bot dari mic, dan
(b) 'barge-in tahan-gema' di server -- saat bot sedang bicara, interupsi HANYA
dikonfirmasi bila terdeteksi bicara BERKELANJUTAN >= VOICEBOT_STREAM_BARGEIN_MIN_MS
dan (untuk fallback RMS) energi >= VOICEBOT_STREAM_SPEAKING_RMS. Blip/gema sesaat
diabaikan sehingga suara bot tidak memotong dirinya sendiri.

Endpointing pakai webrtcvad bila terpasang, kalau tidak jatuh ke VAD energi (RMS).
Tuning via env: VOICEBOT_STREAM_SILENCE_MS (700), VOICEBOT_STREAM_MIN_SPEECH_MS
(250), VOICEBOT_STREAM_PREROLL_MS (300), VOICEBOT_STREAM_RMS (500),
VOICEBOT_STREAM_VAD_AGGR (2), VOICEBOT_STREAM_BARGEIN (1),
VOICEBOT_STREAM_BARGEIN_MIN_MS (500), VOICEBOT_STREAM_SPEAKING_RMS (900).
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


SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_BYTES = int(SAMPLE_RATE * FRAME_MS / 1000) * 2  # 960 byte / frame 30ms


def _env_int(name, default):
    try:
        return int(float(os.environ.get(name, "") or default))
    except Exception:
        return default


def _bargein_enabled():
    return str(os.environ.get("VOICEBOT_STREAM_BARGEIN", "1")) not in ("0", "false", "False")


# --------------------------------------------------------------------- VAD
_VAD = None
_VAD_TRIED = False


def _get_webrtc_vad():
    global _VAD, _VAD_TRIED
    if _VAD_TRIED:
        return _VAD
    _VAD_TRIED = True
    try:
        import webrtcvad  # type: ignore
        aggr = _env_int("VOICEBOT_STREAM_VAD_AGGR", 2)
        aggr = 0 if aggr < 0 else (3 if aggr > 3 else aggr)
        _VAD = webrtcvad.Vad(aggr)
    except Exception:
        _VAD = None
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


def _is_speech(frame: bytes, rms_min=None) -> bool:
    """True bila frame dianggap bicara.

    webrtcvad dipakai bila tersedia (robustnya dari agresivitas VAD + durasi
    berkelanjutan di pemanggil). Bila tidak, fallback ke energi RMS; ambang bisa
    dinaikkan lewat rms_min saat bot sedang bicara agar gema loudspeaker tak
    memicu bicara palsu.
    """
    vad = _get_webrtc_vad()
    if vad is not None and len(frame) == FRAME_BYTES:
        try:
            return bool(vad.is_speech(frame, SAMPLE_RATE))
        except Exception:
            pass
    thr = rms_min if rms_min is not None else _env_int("VOICEBOT_STREAM_RMS", 500)
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
    """VAD sederhana: deteksi awal bicara + akhiri ucapan setelah hening."""

    def __init__(self):
        self.silence_ms = _env_int("VOICEBOT_STREAM_SILENCE_MS", 700)
        self.min_speech_ms = _env_int("VOICEBOT_STREAM_MIN_SPEECH_MS", 250)
        self.preroll_ms = _env_int("VOICEBOT_STREAM_PREROLL_MS", 300)
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
            speech = _is_speech(frame, rms_min)
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
    try:
        await websocket.send_text(json.dumps({
            "type": "ready", "session_id": session_id, "greeting": greeting,
            "sample_rate": SAMPLE_RATE, "bargein": _bargein_enabled(),
        }))
    except Exception:
        return

    state = {"speaking": False, "interrupt": False, "closed": False,
             "gen": 0, "want_audio": True}
    ep = Endpointer()
    queue: asyncio.Queue = asyncio.Queue()

    # tuning barge-in tahan-gema (loudspeaker)
    speaking_rms = _env_int("VOICEBOT_STREAM_SPEAKING_RMS", 900)
    bargein_min_ms = _env_int("VOICEBOT_STREAM_BARGEIN_MIN_MS", 500)

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
                            # saat bot bicara: tahan; barge-in dikonfirmasi di bawah
                        elif ev[0] == "utterance":
                            await queue.put(ev[1])
                    # konfirmasi barge-in: hanya bila bicara BERKELANJUTAN cukup lama
                    if (state["speaking"] and _bargein_enabled()
                            and not state["interrupt"] and ep.triggered
                            and ep.active_speech_ms >= bargein_min_ms):
                        state["interrupt"] = True
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
                    state["want_audio"],
                )
            except Exception as e:  # noqa: BLE001
                await _safe_send_text({"type": "error", "error": str(e)})
                continue
            if state["closed"]:
                break
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
            b64 = res.get("jawaban_audio_b64")
            if b64 and state["want_audio"] and not state["interrupt"] and not state["closed"]:
                await send_audio(b64)

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
