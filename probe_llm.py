# -*- coding: utf-8 -*-
"""probe_llm.py - Uji lapisan LLM Telepon: STT lalu rapikan+ringkas+analisis.

Jalankan dari root repo (venv aktif):
    python probe_llm.py [path_audio.mp4]
Tanpa argumen: pakai berkas awe_audio_*.mp4 terbaru di folder Temp.

Backend LLM mengikuti .env (common.llm_client): LLM_PROVIDER=openai|azure|
gemini|local. Untuk data pajak sensitif pakai LLM_PROVIDER=local (vLLM Qwen)
supaya transkrip tidak keluar dari mesin.
"""
import glob
import os
import sys
import tempfile

import avaya.phone_stt as avstt
import avaya.phone_llm as avllm


def _newest_audio():
    pat = os.path.join(tempfile.gettempdir(), "awe_audio_*.mp4")
    files = sorted(glob.glob(pat), key=lambda p: os.path.getmtime(p), reverse=True)
    return files[0] if files else ""


def _p(label, value):
    print("%-14s: %s" % (label, value))


def _join(xs):
    return ", ".join(str(x) for x in (xs or [])) or "-"


def main(argv):
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass

    path = argv[1] if len(argv) > 1 else _newest_audio()
    if not path:
        print("Tidak ada berkas audio. Klik 'Uji Locator Audio' dulu atau beri path.")
        return 2

    lang = os.environ.get("AWE_STT_LANG") or "id"
    print("[probe_llm] Berkas :", path)
    print("[probe_llm] STT ... (memuat model + transkripsi)")
    tr = avstt.transcribe_file(path, lang=lang)
    if not tr.get("ok"):
        print("STT GAGAL:", tr.get("error"))
        return 1

    print()
    print("===== TRANSKRIP MENTAH (STT) =====")
    _p("device", "%s/%s" % (tr.get("device"), tr.get("compute_type")))
    _p("model", tr.get("model"))
    _p("segmen", tr.get("n_segments"))
    print()
    print(tr.get("text") or "(kosong)")

    print()
    print("[probe_llm] Analisis LLM ... (provider ikut .env)")
    res = avllm.analyze_transcript(tr.get("text") or "", segments=tr.get("segments"))
    if not res.get("ok"):
        print("LLM GAGAL:", res.get("error"))
        return 1

    a = res.get("analysis") or {}
    print()
    print("===== HASIL ANALISIS LLM (%s / %s) =====" % (res.get("provider"), res.get("model")))
    _p("topik", a.get("topik"))
    _p("jenis_layanan", a.get("jenis_layanan"))
    _p("sentimen", a.get("sentimen"))
    _p("emosi", a.get("emosi"))
    _p("resolusi", a.get("resolusi"))
    _p("frustrasi", a.get("frustrasi"))

    print()
    print("--- ringkasan ---")
    print(a.get("ringkasan") or "(kosong)")

    print()
    print("--- dialog rapi ---")
    for turn in (a.get("dialog") or []):
        if isinstance(turn, dict):
            print("%s: %s" % (turn.get("penutur") or "?", turn.get("teks") or ""))

    ent = a.get("entitas") or {}
    if isinstance(ent, dict) and (ent.get("nama") or ent.get("nomor") or ent.get("lainnya")):
        print()
        print("--- entitas ---")
        _p("nama", _join(ent.get("nama")))
        _p("nomor", _join(ent.get("nomor")))
        _p("lainnya", _join(ent.get("lainnya")))

    poin = a.get("poin_penting") or []
    if poin:
        print()
        print("--- poin penting ---")
        for item in poin:
            print("- %s" % item)

    if a.get("catatan_kualitas"):
        print()
        print("--- catatan kualitas ---")
        print(a.get("catatan_kualitas"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
