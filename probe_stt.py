# -*- coding: utf-8 -*-
"""probe_stt.py - uji STT (faster-whisper) dari TERMINAL, bukan lewat web.

Kenapa terpisah: unduh model (~3 GB utk large-v3) + transkripsi itu LAMBAT.
Kalau dijalankan di dalam request web, request menggantung bermenit-menit
lalu balikannya jadi halaman HTML (bukan JSON) -> browser error
"Unexpected token '<'". Jalankan di terminal supaya progres unduh model
kelihatan dan tidak ada timeout.

Pakai (dari root repo, venv aktif):
  python probe_stt.py                 (auto: cari awe_audio_*.mp4 terbaru di Temp)
  python probe_stt.py PATH_KE_AUDIO   (mis. berkas .mp4 hasil unduhan tadi)

Opsi via ENV (opsional):
  AWE_STT_MODEL=small     uji cepat tanpa unduh 3 GB; default large-v3
  AWE_STT_DEVICE=cpu      paksa CPU kalau GPU/cuDNN bermasalah; default auto cuda->cpu
  AWE_STT_COMPUTE=int8    override tipe komputasi
  AWE_STT_LANG=id         bahasa; default id

Instal sekali: pip install faster-whisper
"""
import os
import sys
import glob
import tempfile

import avaya.phone_stt as avstt


def _newest_audio():
    pat = os.path.join(tempfile.gettempdir(), "awe_audio_*.mp4")
    files = glob.glob(pat)
    if not files:
        return ""
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return files[0]


def main(argv):
    path = argv[1] if len(argv) > 1 else _newest_audio()
    if not path:
        print("[probe_stt] Tidak ada berkas audio.")
        print("[probe_stt] Beri path: python probe_stt.py PATH_KE_AUDIO")
        print("[probe_stt] (auto mencari awe_audio_*.mp4 di %s)" % tempfile.gettempdir())
        return 2
    if not os.path.exists(path):
        print("[probe_stt] Berkas tidak ditemukan: %s" % path)
        return 2

    lang = (os.environ.get("AWE_STT_LANG") or "id").strip() or "id"
    print("[probe_stt] Berkas : %s (%d byte)" % (path, os.path.getsize(path)))
    print("[probe_stt] Model  : %s" % (os.environ.get("AWE_STT_MODEL") or "large-v3"))
    print("[probe_stt] Device : %s (env AWE_STT_DEVICE; default auto cuda->cpu)" % (os.environ.get("AWE_STT_DEVICE") or "auto"))
    print("[probe_stt] Bahasa : %s" % lang)
    print("[probe_stt] Memuat model + transkripsi... (jika model belum ada, mengunduh dulu; bisa beberapa menit)")

    tr = avstt.transcribe_file(path, lang=lang)

    print("")
    if not tr.get("ok"):
        print("[probe_stt] GAGAL: %s" % tr.get("error"))
        return 1

    print("===== HASIL STT =====")
    print("device        : %s" % tr.get("device"))
    print("compute       : %s" % tr.get("compute_type"))
    print("model         : %s" % tr.get("model"))
    print("bahasa        : %s" % tr.get("language"))
    print("durasi audio  : %s dtk" % tr.get("duration"))
    print("jumlah segmen : %s" % tr.get("n_segments"))
    print("waktu proses  : %s dtk" % tr.get("elapsed_sec"))
    print("")
    print("--- transkrip penuh ---")
    print(tr.get("text") or "(kosong)")
    print("")
    print("--- segmen (waktu) ---")
    for s in (tr.get("segments") or []):
        print("[%6.2f -> %6.2f] %s" % (float(s.get("start") or 0.0), float(s.get("end") or 0.0), s.get("text") or ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
