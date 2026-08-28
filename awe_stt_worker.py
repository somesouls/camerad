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
       "chunks": 3, "elapsed": 12.3, "model": "...", "device": "cuda:0"}, ...]}

Env: sama seperti probe_multimodal (AWE_QWEN_MODEL/LANG/CHUNK_SEC/DEVICE/DTYPE/
MAXTOK, AWE_ASR_SAMPLES).
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

    from probe_multimodal import qwen_transcribe

    results = []
    t0 = time.time()
    for i, f in enumerate(files, 1):
        print("[worker] %d/%d %s" % (i, len(files), os.path.basename(f)), file=sys.stderr)
        r = qwen_transcribe(f)
        results.append({
            "file": f,
            "ok": bool(r.get("ok")),
            "text": r.get("text") or "",
            "language": r.get("language") or "",
            "chunks": r.get("chunks") or 0,
            "elapsed": r.get("elapsed") or 0.0,
            "model": r.get("model") or "",
            "device": r.get("device") or "",
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
