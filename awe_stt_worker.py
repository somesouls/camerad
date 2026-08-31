# -*- coding: utf-8 -*-
"""awe_stt_worker.py - Worker STT batch Qwen3-ASR (jalan di .venv-asr).

Jembatan STT untuk app utama. qwen-asr butuh transformers==4.57.6 yang bentrok
dengan venv pipeline utama, jadi transkripsi Qwen dijalankan di venv terpisah
(.venv-asr) via subprocess. Worker ini memuat model SEKALI lalu mentranskrip
banyak berkas (sudah dipotong/anti-loop lewat probe_multimodal.qwen_transcribe)
dan menulis hasil sebagai JSON.

Dipanggil app utama, contoh:
    .venv-asr/Scripts/python.exe awe_stt_worker.py --out hasil.json a.mp4 b.mp4
Boleh juga: folder (ambil *.mp4 di dalamnya), atau tanpa argumen (ambil
awe_audio_*.mp4 terbaru di %TEMP%, jumlah = AWE_ASR_SAMPLES, default 3).

Keluaran JSON:
    {"ok": true, "count": N, "elapsed": S, "results": [
      {"file": "...", "ok": true, "text": "...", "language": "Indonesian",
       "chunks": 3, "elapsed": 12.3, "model": "...", "device": "cuda:0",
       "dual": false, "channels": []}, ...]}

Env: sama seperti probe_multimodal (AWE_QWEN_MODEL/LANG/CHUNK_SEC/DEVICE/DTYPE/
MAXTOK, AWE_ASR_SAMPLES). Tambahan: AWE_QWEN_CONTEXT = teks konteks/kosakata
(hotwords) domain untuk membiaskan Qwen3-ASR (disuntik app utama dari Glosarium
Pajak; lihat avaya/phone_glossary.asr_context). Tambahan: AWE_STT_DUAL_CHANNEL
(1/true/yes) memisah audio stereo per kanal (agen vs penelepon) via awe_stt_dual;
audio mono otomatis fallback ke jalur biasa.
"""
import glob
import json
import os
import sys
import time


def _collect(inputs):
    files = []
    for a in inputs:
        if os.path.isdir(a):
            files += sorted(glob.glob(os.path.join(a, "*.mp4")))
        elif os.path.exists(a):
            files.append(a)
        else:
            print("(lewati, tak ada) %s" % a, file=sys.stderr)
    seen = set()
    uniq = []
    for f in files:
        key = os.path.abspath(f)
        if key not in seen:
            seen.add(key)
            uniq.append(f)
    return uniq


def main(argv):
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass

    out_path = None
    inputs = []
    it = iter(argv[1:])
    for a in it:
        if a in ("--out", "-o"):
            out_path = next(it, None)
        elif a in ("--in", "-i"):
            nxt = next(it, None)
            if nxt:
                inputs.append(nxt)
        else:
            inputs.append(a)

    files = _collect(inputs)
    if not files:
        try:
            from probe_multimodal import _newest_audio
            n = int(os.environ.get("AWE_ASR_SAMPLES") or 3)
            files = _newest_audio(n)
        except Exception:
            files = []

    if not files:
        err = {"ok": False, "count": 0, "results": [], "error": "tak ada berkas audio"}
        js = json.dumps(err, ensure_ascii=False)
        if out_path:
            with open(out_path, "w", encoding="utf-8") as w:
                w.write(js)
        else:
            print(js)
        print("[worker] tak ada berkas audio", file=sys.stderr)
        return 2

    import probe_multimodal as _pm
    from probe_multimodal import qwen_transcribe

    # Konteks/kosakata domain (hotwords) untuk membiaskan Qwen3-ASR. Disuntik
    # lewat env AWE_QWEN_CONTEXT oleh app utama (Glosarium Pajak). qwen_transcribe
    # memanggil m.transcribe(audio=..., language=...) tanpa context, jadi kita
    # muat model lebih awal lalu bungkus .transcribe agar context ikut terkirim.
    # Semua dibungkus try/except supaya STT tetap jalan bila gagal/param beda versi.
    _ctx = (os.environ.get("AWE_QWEN_CONTEXT") or "").strip()
    if _ctx:
        try:
            _m = _pm._qwen_model()[0]
            _orig_tx = _m.transcribe

            def _tx_with_ctx(*a, **k):
                if "context" not in k:
                    try:
                        return _orig_tx(*a, context=_ctx, **k)
                    except TypeError:
                        return _orig_tx(*a, **k)
                return _orig_tx(*a, **k)

            _m.transcribe = _tx_with_ctx
            print("[worker] konteks STT (glosarium) aktif: %d char" % len(_ctx), file=sys.stderr)
        except Exception as e:
            print("[worker] konteks STT dilewati: %r" % e, file=sys.stderr)

    # Mode DWI-KANAL (opsional): pisah audio stereo -> transkrip tiap kanal
    # sebagai satu penutur (agen vs penelepon). Mono otomatis fallback -> aman.
    _dual = (os.environ.get("AWE_STT_DUAL_CHANNEL") or "").strip().lower() in ("1", "true", "yes")
    _dual_fn = None
    _probe_ch = None
    if _dual:
        try:
            from awe_stt_dual import qwen_transcribe_dual as _dual_fn
            from awe_stt_dual import probe_channels as _probe_ch
            print("[worker] mode DWI-KANAL aktif (AWE_STT_DUAL_CHANNEL)", file=sys.stderr)
        except Exception as e:
            print("[worker] modul dwi-kanal gagal, pakai mono: %r" % e, file=sys.stderr)
            _dual_fn = None
            _probe_ch = None

    results = []
    t0 = time.time()
    for i, f in enumerate(files, 1):
        nch = None
        if _probe_ch is not None:
            try:
                nch = _probe_ch(f)
            except Exception:
                nch = None
        extra = (" (kanal=%s)" % nch) if nch is not None else ""
        print("[worker] %d/%d %s%s" % (i, len(files), os.path.basename(f), extra), file=sys.stderr)
        r = _dual_fn(f) if _dual_fn else qwen_transcribe(f)
        results.append({
            "file": f,
            "ok": bool(r.get("ok")),
            "text": r.get("text") or "",
            "language": r.get("language") or "",
            "chunks": r.get("chunks") or 0,
            "elapsed": r.get("elapsed") or 0.0,
            "model": r.get("model") or "",
            "device": r.get("device") or "",
            "dual": bool(r.get("dual")),
            "channels": r.get("channels") or [],
            "error": r.get("error"),
        })

    data = {"ok": True, "count": len(results),
            "elapsed": round(time.time() - t0, 2), "results": results}
    js = json.dumps(data, ensure_ascii=False, indent=2)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as w:
            w.write(js)
        print("[worker] tulis %d hasil -> %s" % (len(results), out_path), file=sys.stderr)
    else:
        print(js)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
