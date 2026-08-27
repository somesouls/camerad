# -*- coding: utf-8 -*-
"""avaya/phone_stt.py - STT lokal (faster-whisper) untuk audio Telepon (2c-probe).

Mendekode + mentranskrip berkas audio hasil unduhan (.mp4 fMP4 AAC) dari
phone_dash.download_and_save memakai faster-whisper. faster-whisper mendekode
via PyAV (pustaka ffmpeg bawaan) sehingga ffmpeg SISTEM tidak wajib.

Backend otomatis: coba CUDA float16 dulu, lalu fallback CPU int8. Model
di-cache di memori proses (tidak reload tiap panggilan). Dekode dipasang
anti-ulang/anti-halusinasi (VAD, condition_on_previous_text=False, temperature
fallback, repetition_penalty, no_repeat_ngram) - penting utk audio telepon
8 kHz yang gampang bikin Whisper mengulang. Override via env:
  AWE_STT_MODEL   (default 'large-v3'; pakai 'small'/'medium' utk uji cepat)
  AWE_STT_DEVICE  ('cuda' | 'cpu')
  AWE_STT_COMPUTE ('float16' | 'int8' | 'int8_float16' | ...)
  AWE_STT_BEAM    (ukuran beam; default 5)
  AWE_STT_VAD     ('1' aktif / '0' nonaktif VAD; default '1')
  AWE_STT_PROMPT  (initial_prompt domain, opsional; default kosong)

Instal (venv): pip install faster-whisper
Model large-v3 (~3 GB) diunduh otomatis saat pertama dipakai.
Khusus uji: TIDAK menyimpan transkrip ke DB, TIDAK menyentuh kredensial.
"""
import os
import time

_MODEL_CACHE = {}


def _load_model(model_size, device, compute_type):
    key = (model_size, device, compute_type)
    m = _MODEL_CACHE.get(key)
    if m is None:
        from faster_whisper import WhisperModel
        m = WhisperModel(model_size, device=device, compute_type=compute_type)
        _MODEL_CACHE[key] = m
    return m


def _backend_order():
    dev = (os.environ.get("AWE_STT_DEVICE") or "").strip().lower()
    ct = (os.environ.get("AWE_STT_COMPUTE") or "").strip().lower()
    if dev == "cpu":
        return [("cpu", ct or "int8")]
    return [("cuda", ct or "float16"), ("cpu", ct or "int8")]


def _decode_opts(beam_size):
    """Opsi dekode anti-ulang/anti-halusinasi utk audio telepon 8 kHz."""
    try:
        beam = int(os.environ.get("AWE_STT_BEAM") or beam_size)
    except Exception:
        beam = beam_size
    vad = (os.environ.get("AWE_STT_VAD") or "1").strip().lower() not in ("0", "false", "no")
    prompt = (os.environ.get("AWE_STT_PROMPT") or "").strip() or None
    opts = {
        "beam_size": beam,
        "temperature": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        "condition_on_previous_text": False,
        "compression_ratio_threshold": 2.4,
        "log_prob_threshold": -1.0,
        "no_speech_threshold": 0.6,
        "repetition_penalty": 1.1,
        "no_repeat_ngram_size": 3,
        "vad_filter": vad,
        "vad_parameters": {"min_silence_duration_ms": 500},
    }
    if prompt:
        opts["initial_prompt"] = prompt
    return opts


def transcribe_file(path, lang="id", model_size=None, beam_size=5, max_seg=120):
    """Transkrip satu berkas audio; kembalikan dict ringkas & aman untuk JSON."""
    out = {"ok": False, "path": path or "", "model": "", "device": "",
           "compute_type": "", "language": "", "duration": 0.0, "n_segments": 0,
           "text": "", "segments": [], "elapsed_sec": 0.0}
    if not path or not os.path.exists(path):
        out["error"] = "Berkas audio tidak ditemukan: %s" % (path or "(kosong)")
        return out
    model_size = model_size or (os.environ.get("AWE_STT_MODEL") or "large-v3")
    out["model"] = model_size
    t0 = time.time()
    model = None
    last_err = None
    for device, compute_type in _backend_order():
        try:
            model = _load_model(model_size, device, compute_type)
            out["device"] = device
            out["compute_type"] = compute_type
            break
        except Exception as e:
            last_err = e
            model = None
    if model is None:
        out["elapsed_sec"] = round(time.time() - t0, 2)
        out["error"] = "Gagal memuat model STT: %r (pip install faster-whisper)" % last_err
        return out
    opts = _decode_opts(beam_size)
    try:
        try:
            segments, info = model.transcribe(path, language=(lang or None), **opts)
        except TypeError:
            segments, info = model.transcribe(
                path, language=(lang or None),
                beam_size=opts.get("beam_size", 5),
                condition_on_previous_text=False,
                vad_filter=opts.get("vad_filter", True))
        out["language"] = getattr(info, "language", "") or (lang or "")
        out["duration"] = round(float(getattr(info, "duration", 0.0) or 0.0), 3)
        texts = []
        seg_list = []
        for s in segments:
            txt = (getattr(s, "text", "") or "").strip()
            if txt:
                texts.append(txt)
            if len(seg_list) < max_seg:
                seg_list.append({"start": round(float(getattr(s, "start", 0.0) or 0.0), 2),
                                 "end": round(float(getattr(s, "end", 0.0) or 0.0), 2),
                                 "text": txt})
        out["segments"] = seg_list
        out["n_segments"] = len(texts)
        out["text"] = " ".join(texts).strip()
        out["ok"] = True
    except Exception as e:
        out["error"] = "Transkripsi gagal: %r" % e
    out["elapsed_sec"] = round(time.time() - t0, 2)
    return out


def stt_summary_rows(tr):
    """Baris ringkasan utk kartu uji (bentuk sama dgn media_summary)."""
    tr = tr or {}
    if tr.get("ok"):
        det = "%s/%s • %s seg • %.1f dtk audio • %ss wall" % (
            tr.get("device") or "?", tr.get("compute_type") or "?",
            tr.get("n_segments") or 0, float(tr.get("duration") or 0.0),
            tr.get("elapsed_sec") or 0)
        preview = (tr.get("text") or "")[:180] or "(kosong)"
        return [{"item": "STT (faster-whisper %s)" % (tr.get("model") or "?"),
                 "http": "ok", "locator_status": "", "encryption": "", "detail": det},
                {"item": "Transkrip (potongan)", "http": "", "locator_status": "",
                 "encryption": "", "detail": preview}]
    return [{"item": "STT (faster-whisper)", "http": "GAGAL", "locator_status": "",
             "encryption": "", "detail": ("ERROR: " + str(tr.get("error"))[:140]) if tr.get("error") else "gagal"}]


def attach_transcript(res, lang="id"):
    """Baca saved_path dari hasil probe_media, transkrip, tempel ke res (mutasi)."""
    if not isinstance(res, dict) or not res.get("found_row"):
        return res
    dl = (res.get("media_raw") or {}).get("download") or {}
    path = dl.get("saved_path") or ""
    tr = transcribe_file(path, lang=lang)
    res.setdefault("media_summary", []).extend(stt_summary_rows(tr))
    res.setdefault("media_raw", {})["transcript"] = tr
    res["transcript_text"] = tr.get("text") or ""
    base = str(res.get("http_status") or "")
    if tr.get("ok"):
        res["http_status"] = base + " | STT %s seg (%s/%s, %ss)" % (
            tr.get("n_segments"), tr.get("device"), tr.get("compute_type"),
            tr.get("elapsed_sec"))
    else:
        res["http_status"] = base + " | STT GAGAL"
    return res
