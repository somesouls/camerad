# -*- coding: utf-8 -*-
"""probe_multimodal.py - Sanding STT: Whisper large-v3 vs Qwen3-ASR.

Uji banding transkripsi untuk 2-3 sampel audio Telepon (.mp4 8 kHz mono):
  A. faster-whisper large-v3 (baseline yang selama ini dipakai).
  B. Qwen3-ASR (paket 'qwen-asr') - ASR khusus, klaim ungguli Whisper large-v3
     multibahasa termasuk Indonesia; model 1.7B (~3.5 GB).

STANDALONE: tidak mengimpor modul repo, jadi AMAN dijalankan di venv TERPISAH
supaya paket qwen-asr (menarik transformers 4.57.6) tidak mengganggu venv
pipeline utama (sentence-transformers). Saran setup:
    python -m venv .venv-asr
    (Windows: .venv-asr/Scripts/activate atau Activate.ps1)
    pip install -U faster-whisper qwen-asr
    python probe_multimodal.py

CATATAN CUDA (penting): venv baru biasanya dapat torch CPU-only, sehingga
Qwen3-ASR jatuh ke CPU (akurasi tetap valid, hanya lambat) dan faster-whisper
tidak menemukan cublas lalu jatuh ke CPU juga. Untuk GPU, pasang torch CUDA
yang SAMA dengan venv utama, contoh:
    pip install --force-reinstall torch --index-url https://download.pytorch.org/whl/cu121
Cek versi cuXXX venv utama dengan:
    python -c "import torch; print(torch.__version__)"

faster-whisper dipakai sekaligus sebagai dekoder audio (mp4 8 kHz -> wav 16 kHz
mono) untuk sisi Qwen, jadi pasang keduanya. Tanpa argumen: ambil beberapa
awe_audio_*.mp4 terbaru di folder Temp. Dengan argumen: beri path .mp4 sendiri.

Env opsional:
  AWE_ASR_SAMPLES       jumlah berkas terbaru bila tanpa argumen (default 3)
  AWE_ASR_SKIP_WHISPER  '1' = lewati sisi Whisper
  AWE_ASR_SKIP_QWEN     '1' = lewati sisi Qwen3-ASR
  AWE_QWEN_MODEL        repo Qwen3-ASR (default Qwen/Qwen3-ASR-1.7B; ...-0.6B ringan)
  AWE_QWEN_LANG         paksa bahasa (default Indonesian; set kosong = auto-deteksi)
  AWE_QWEN_MAXTOK       batas token keluaran (default 1024)
  AWE_QWEN_DTYPE        bfloat16 | float16 | float32 (default auto: cpu=float32)
  AWE_QWEN_DEVICE       cuda:0 | cpu (default auto: cuda bila tersedia)
  AWE_STT_MODEL         model whisper (default large-v3); AWE_STT_* lain spt phone_stt.
"""
import glob
import os
import sys
import tempfile
import time
import wave

_WHISPER_CACHE = {}
_QWEN_CACHE = {}


def _attr(o, k):
    return o.get(k) if isinstance(o, dict) else getattr(o, k, None)


def _flag(name):
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes")


def _newest_audio(n):
    pat = os.path.join(tempfile.gettempdir(), "awe_audio_*.mp4")
    files = sorted(glob.glob(pat), key=lambda p: os.path.getmtime(p), reverse=True)
    return files[:max(1, n)]


def _decode_opts(beam_size=5):
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


def whisper_transcribe(path):
    from faster_whisper import WhisperModel
    size = os.environ.get("AWE_STT_MODEL") or "large-v3"
    dev = (os.environ.get("AWE_STT_DEVICE") or "").strip().lower()
    ct = (os.environ.get("AWE_STT_COMPUTE") or "").strip().lower()
    order = [("cpu", ct or "int8")] if dev == "cpu" else [("cuda", ct or "float16"), ("cpu", ct or "int8")]
    lang = os.environ.get("AWE_STT_LANG") or "id"
    opts = _decode_opts()
    out = {"model": size, "device": "", "text": "", "elapsed": 0.0, "language": "", "ok": False}
    t0 = time.time()
    last = None
    for device, compute in order:
        key = (size, device, compute)
        try:
            m = _WHISPER_CACHE.get(key)
            if m is None:
                m = WhisperModel(size, device=device, compute_type=compute)
                _WHISPER_CACHE[key] = m
            if device != "cuda":
                print("  [whisper] pakai %s/%s (lambat, bisa beberapa menit)" % (device, compute))
            try:
                segments, info = m.transcribe(path, language=lang, **opts)
            except TypeError:
                segments, info = m.transcribe(path, language=lang,
                                              beam_size=opts.get("beam_size", 5),
                                              condition_on_previous_text=False,
                                              vad_filter=opts.get("vad_filter", True))
            texts = [(getattr(s, "text", "") or "").strip() for s in segments]
            out["text"] = " ".join(t for t in texts if t).strip()
            out["language"] = getattr(info, "language", "") or ""
            out["device"] = "%s/%s" % (device, compute)
            out["ok"] = True
            break
        except Exception as e:
            last = e
            _WHISPER_CACHE.pop(key, None)
    if not out["ok"]:
        out["error"] = repr(last)
    out["elapsed"] = round(time.time() - t0, 2)
    return out


def _to_wav16k(mp4_path):
    from faster_whisper.audio import decode_audio
    import numpy as np
    pcm = np.asarray(decode_audio(mp4_path, sampling_rate=16000), dtype="float32")
    pcm16 = (np.clip(pcm, -1.0, 1.0) * 32767.0).astype("<i2")
    wav_path = mp4_path + ".q16k.wav"
    with wave.open(wav_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm16.tobytes())
    return wav_path


def _qwen_model():
    if "m" in _QWEN_CACHE:
        return _QWEN_CACHE["m"]
    import torch
    from qwen_asr import Qwen3ASRModel
    model_id = os.environ.get("AWE_QWEN_MODEL") or "Qwen/Qwen3-ASR-1.7B"
    try:
        maxtok = int(os.environ.get("AWE_QWEN_MAXTOK") or 1024)
    except Exception:
        maxtok = 1024
    dev = (os.environ.get("AWE_QWEN_DEVICE") or "").strip()
    if not dev:
        dev = "cuda:0" if torch.cuda.is_available() else "cpu"
    tries = [dev] if dev.startswith("cpu") else [dev, "cpu"]
    dmap = {"bfloat16": torch.bfloat16, "float16": torch.float16, "float32": torch.float32}
    last = None
    for d in tries:
        name = (os.environ.get("AWE_QWEN_DTYPE") or "").strip().lower()
        if not name:
            name = "float32" if d.startswith("cpu") else "bfloat16"
        dtype = dmap.get(name, torch.float32)
        if d.startswith("cpu"):
            print("  [qwen] pakai CPU (lambat); pasang torch CUDA utk GPU")
        try:
            try:
                m = Qwen3ASRModel.from_pretrained(model_id, dtype=dtype, device_map=d,
                                                  max_inference_batch_size=8, max_new_tokens=maxtok)
            except TypeError:
                m = Qwen3ASRModel.from_pretrained(model_id, torch_dtype=dtype,
                                                  device_map=d, max_new_tokens=maxtok)
            _QWEN_CACHE["m"] = (m, model_id, d)
            return _QWEN_CACHE["m"]
        except Exception as e:
            last = e
    raise RuntimeError("gagal load Qwen3-ASR: %r" % last)


def qwen_transcribe(wav_path):
    out = {"model": "", "device": "", "text": "", "elapsed": 0.0, "language": "", "ok": False}
    t0 = time.time()
    try:
        m, model_id, dev = _qwen_model()
        out["model"] = model_id
        out["device"] = dev
        raw = os.environ.get("AWE_QWEN_LANG", "Indonesian")
        lang = (raw.strip() or None) if raw is not None else None
        results = m.transcribe(audio=wav_path, language=lang)
        r0 = results[0]
        out["text"] = (_attr(r0, "text") or "").strip()
        out["language"] = _attr(r0, "language") or ""
        out["ok"] = True
    except Exception as e:
        out["error"] = repr(e)
    out["elapsed"] = round(time.time() - t0, 2)
    return out


def _hr(c="="):
    return c * 72


def main(argv):
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass

    args = [a for a in argv[1:] if a.strip()]
    if args:
        paths = [p for p in args if os.path.exists(p)]
        for p in [p for p in args if not os.path.exists(p)]:
            print("(lewati, tak ada) %s" % p)
    else:
        try:
            n = int(os.environ.get("AWE_ASR_SAMPLES") or 3)
        except Exception:
            n = 3
        paths = _newest_audio(n)

    if not paths:
        print("Tidak ada audio. Klik 'Uji Locator Audio' di /awe/telepon dulu, atau beri path .mp4.")
        return 2

    have_whisper = False
    if _flag("AWE_ASR_SKIP_WHISPER"):
        print("[info] AWE_ASR_SKIP_WHISPER aktif; sisi Whisper dilewati.")
    else:
        try:
            import faster_whisper  # noqa: F401
            have_whisper = True
        except Exception:
            print("[info] faster-whisper tidak terpasang; sisi Whisper dilewati. pip install faster-whisper")

    have_qwen = False
    if _flag("AWE_ASR_SKIP_QWEN"):
        print("[info] AWE_ASR_SKIP_QWEN aktif; sisi Qwen3-ASR dilewati.")
    else:
        try:
            import qwen_asr  # noqa: F401
            have_qwen = True
        except Exception:
            print("[info] qwen-asr tidak terpasang; sisi Qwen3-ASR dilewati. pip install qwen-asr")

    if not (have_whisper or have_qwen):
        print("Tidak ada backend STT aktif.")
        return 1

    print(_hr())
    print("SANDING STT  |  %d sampel  |  Whisper=%s  Qwen3-ASR=%s" % (
        len(paths), "ya" if have_whisper else "-", "ya" if have_qwen else "-"))
    print(_hr())

    timings = []
    for i, path in enumerate(paths, 1):
        print()
        print("[%d/%d] %s" % (i, len(paths), path))
        wres = qres = None
        if have_whisper:
            print("  ... Whisper %s" % (os.environ.get("AWE_STT_MODEL") or "large-v3"))
            wres = whisper_transcribe(path)
        if have_qwen:
            print("  ... Qwen3-ASR (dekode 16 kHz + transkripsi)")
            wav = ""
            try:
                wav = _to_wav16k(path)
            except Exception as e:
                print("  [wav16k GAGAL] %r" % e)
            if wav:
                qres = qwen_transcribe(wav)
                try:
                    os.remove(wav)
                except Exception:
                    pass

        print()
        if wres:
            print("  --- Whisper %s  (%s, %ss, lang=%s) ---" % (
                wres.get("model") or "?", wres.get("device") or "?", wres.get("elapsed"), wres.get("language") or "?"))
            body = (wres.get("text") or "(kosong)") if wres.get("ok") else "ERROR: " + str(wres.get("error"))
            print("  " + body)
        if qres:
            print()
            print("  --- Qwen3-ASR %s  (%s, %ss, lang=%s) ---" % (
                qres.get("model") or "?", qres.get("device") or "?", qres.get("elapsed"), qres.get("language") or "?"))
            body = (qres.get("text") or "(kosong)") if qres.get("ok") else "ERROR: " + str(qres.get("error"))
            print("  " + body)
        timings.append((path, wres, qres))

    print()
    print(_hr())
    print("RINGKASAN WAKTU (detik)")
    print(_hr())
    print("%-30s %10s %10s" % ("berkas", "whisper", "qwen3-asr"))
    for path, wres, qres in timings:
        name = os.path.basename(path)
        if len(name) > 30:
            name = name[:27] + "..."
        wt = ("%.2f" % wres["elapsed"]) if (wres and wres.get("ok")) else ("x" if wres else "-")
        qt = ("%.2f" % qres["elapsed"]) if (qres and qres.get("ok")) else ("x" if qres else "-")
        print("%-30s %10s %10s" % (name, wt, qt))
    print()
    print("Nilai 'akurat' dibaca manual: cek nama, deret angka, dan istilah domain")
    print("(mis. 'tiket melati' / MELATI). WER butuh ground-truth manual.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
