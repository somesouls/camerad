# -*- coding: utf-8 -*-
"""awe_stt_dual.py - Transkripsi Qwen3-ASR DWI-KANAL (agen vs penelepon).

Berjalan di .venv-asr; dipanggil awe_stt_worker.py saat AWE_STT_DUAL_CHANNEL
aktif. Memisahkan audio stereo jadi 2 kanal (mis. agen di satu kanal, penelepon
di kanal lain) lalu mentranskrip TIAP kanal sebagai satu penutur murni memakai
Qwen3-ASR (lewat helper probe_multimodal). Tiap potongan diberi perkiraan waktu
dari offset sampel sehingga app utama bisa mengurutkan giliran lintas-kanal.

Qwen tanpa forced_aligner tak punya timestamp kata; urutan giliran memakai
granularitas potongan (~AWE_QWEN_CHUNK_SEC). Bila audio bukan stereo (mono/1
kanal, atau kiri==kanan), otomatis kembali ke jalur mono biasa
(probe_multimodal.qwen_transcribe) sehingga TANPA risiko untuk data mono.

Env: AWE_STT_DUAL_CHANNEL (1/true/yes utk mengaktifkan; dibaca di worker).
"""
import os
import time

import probe_multimodal as _pm

_NL = chr(10)


def probe_channels(path):
    """Jumlah kanal audio berkas (defensif; 0 bila gagal dideteksi)."""
    try:
        import av
    except Exception:
        return 0
    try:
        with av.open(path) as c:
            for s in c.streams:
                if s.type != "audio":
                    continue
                ch = getattr(s, "channels", None)
                if ch:
                    return int(ch)
                cc = getattr(s, "codec_context", None)
                if cc is not None and getattr(cc, "channels", None):
                    return int(cc.channels)
                lay = getattr(s, "layout", None)
                chans = getattr(lay, "channels", None) if lay is not None else None
                if chans:
                    try:
                        return int(len(chans))
                    except Exception:
                        pass
    except Exception:
        return 0
    return 0


def _decode_split_16k(path):
    """List array float32 per kanal @16 kHz. Fallback aman: [mono]."""
    import numpy as np
    try:
        from faster_whisper.audio import decode_audio
    except Exception:
        return [_pm._decode_pcm16k(path)]
    try:
        pair = decode_audio(path, sampling_rate=16000, split_stereo=True)
    except Exception:
        return [_pm._decode_pcm16k(path)]
    if isinstance(pair, (list, tuple)) and len(pair) == 2:
        a = np.asarray(pair[0], dtype="float32")
        b = np.asarray(pair[1], dtype="float32")
        if a.size and b.size:
            n = int(min(a.size, b.size, 16000 * 5))
            if n > 0 and float(np.abs(a[:n] - b[:n]).mean()) < 1e-6:
                return [a]
            return [a, b]
        if a.size:
            return [a]
        if b.size:
            return [b]
    try:
        arr = np.asarray(pair, dtype="float32")
        if arr.ndim >= 1 and arr.size:
            return [arr.reshape(-1)]
    except Exception:
        pass
    return [_pm._decode_pcm16k(path)]


def qwen_transcribe_dual(path):
    """Transkrip Qwen per kanal. Bila mono -> qwen_transcribe biasa.

    Kembalikan dict: {ok, text, language, chunks, elapsed, model, device,
    dual, channels:[{ch, text, segments:[{start,end,text}]}], error?}
    """
    out = {"model": "", "device": "", "text": "", "elapsed": 0.0,
           "language": "", "ok": False, "chunks": 0, "dual": False,
           "channels": []}
    t0 = time.time()
    tmps = []
    try:
        chans = _decode_split_16k(path)
        if not chans:
            raise RuntimeError("dekode audio gagal")
        if len(chans) < 2:
            r = _pm.qwen_transcribe(path)
            r["dual"] = False
            r["channels"] = []
            return r
        try:
            win_sec = float(os.environ.get("AWE_QWEN_CHUNK_SEC") or 30.0)
        except Exception:
            win_sec = 30.0
        m, model_id, dev = _pm._qwen_model()
        out["model"] = model_id
        out["device"] = dev
        raw = os.environ.get("AWE_QWEN_LANG", "Indonesian")
        lang = (raw.strip() or None) if raw is not None else None
        total = 0
        lang_seen = ""
        ch_results = []
        for ci, pcm in enumerate(chans[:2]):
            bounds = _pm._chunk_bounds(pcm, 16000, win_sec)
            texts = []
            segs = []
            for i, (s, e) in enumerate(bounds):
                wav_path = "%s.d%d.%02d.wav" % (path, ci, i)
                _pm._write_wav16k(pcm[s:e], wav_path)
                tmps.append(wav_path)
                results = m.transcribe(audio=wav_path, language=lang)
                r0 = results[0]
                t = (_pm._attr(r0, "text") or "").strip()
                lang_seen = lang_seen or (_pm._attr(r0, "language") or "")
                if t:
                    texts.append(t)
                    segs.append({"start": round(s / 16000.0, 2),
                                 "end": round(e / 16000.0, 2), "text": t})
            total += len(bounds)
            ch_results.append({"ch": ci, "text": " ".join(texts).strip(),
                               "segments": segs})
        out["channels"] = ch_results
        out["chunks"] = total
        out["language"] = lang_seen
        out["dual"] = True
        lines = []
        for c in ch_results:
            lines.append("=== kanal %d ===" % c["ch"])
            lines.append(c["text"] or "(kosong)")
        out["text"] = _NL.join(lines).strip()
        out["ok"] = True
    except Exception as e:
        out["error"] = repr(e)
    finally:
        for p in tmps:
            try:
                os.remove(p)
            except Exception:
                pass
    out["elapsed"] = round(time.time() - t0, 2)
    return out
