#!/usr/bin/env python3
"""
enhance.py - Face enhancer pasca Wav2Lip (GFPGAN) untuk OtoPost.

KENAPA: model Wav2Lip hanya meng-generate mulut pada 96x96 lalu di-upscale, jadi
mulut buram & "buka-tutup". GFPGAN merestorasi seluruh wajah tiap frame sehingga
mulut lebih tajam dan MENYATU dengan wajah asli -> hasil lipsync lebih natural.

DIPANGGIL server (lipsync_engine.py) sebagai langkah kedua:
    Wav2Lip -> kasar.mp4 -> enhance.py -> final.mp4

PEMAKAIAN:
    python enhance.py --input in.mp4 --output out.mp4 \
        --model C:\\wav2lip\\gfpgan\\GFPGANv1.4.pth \
        --strength 0.8 --only_center_face 1 --upscale 1

FAIL-SAFE: bila GFPGAN/basicsr belum terpasang atau error, skrip keluar dengan
kode !=0 dan TIDAK menulis output; server otomatis memakai video Wav2Lip apa adanya
(kualitas lama), jadi pipeline tidak pernah rusak.

PENTING (perbaikan deadlock): stderr ffmpeg diarahkan ke file log, TIDAK ke PIPE.
Menyimpan stderr sebagai PIPE tanpa dibaca membuat buffer penuh -> ffmpeg berhenti
membaca stdin -> penulisan frame macet -> proses idle (CPU/GPU ~0%) sampai timeout.

CATATAN torch/torchvision baru (>=0.17): basicsr sering gagal import
'torchvision.transforms.functional_tensor'. Perbaiki 1 baris di file:
  <venv>/Lib/site-packages/basicsr/data/degradations.py
    from torchvision.transforms.functional_tensor import rgb_to_grayscale
  ganti jadi:
    from torchvision.transforms.functional import rgb_to_grayscale
"""
import os
import sys
import argparse
import subprocess


def log(*a):
    print("[enhance]", *a, flush=True)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--model", default=os.environ.get("GFPGAN_MODEL", ""))
    p.add_argument("--strength", type=float,
                   default=float(os.environ.get("ENHANCE_STRENGTH", "0.8")))
    p.add_argument("--upscale", type=int,
                   default=int(os.environ.get("ENHANCE_UPSCALE", "1")))
    p.add_argument("--only_center_face", type=int,
                   default=int(os.environ.get("ENHANCE_ONLY_CENTER_FACE", "1")))
    p.add_argument("--crf", type=int,
                   default=int(os.environ.get("ENHANCE_CRF", "18")))
    return p.parse_args()


def main():
    args = parse_args()
    if not os.path.isfile(args.input):
        log("input tidak ada:", args.input)
        return 2
    try:
        import cv2
        import torch
    except Exception as e:
        log("gagal import cv2/torch:", e)
        return 3
    try:
        from gfpgan import GFPGANer
    except Exception as e:
        log("GFPGAN tidak terpasang / gagal import "
            "(cek patch basicsr functional_tensor):", e)
        return 4

    model_path = (args.model or "").strip()
    if not model_path or not os.path.isfile(model_path):
        log("model GFPGAN tidak ditemukan:", model_path)
        return 5

    device = "cuda" if torch.cuda.is_available() else "cpu"
    strength = max(0.0, min(1.0, args.strength))
    log("device=", device, "strength=", strength, "model=", model_path)

    try:
        restorer = GFPGANer(
            model_path=model_path,
            upscale=max(1, args.upscale),
            arch="clean",
            channel_multiplier=2,
            bg_upsampler=None,  # hemat VRAM: latar tidak di-upscale
            device=torch.device(device),
        )
    except Exception as e:
        log("gagal inisialisasi GFPGANer:", e)
        return 6

    cap = cv2.VideoCapture(args.input)
    if not cap.isOpened():
        log("gagal buka video input")
        return 7
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    if fps <= 0:
        fps = 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    log("mulai proses", total, "frame @", round(fps, 3), "fps")

    ff = None
    ff_log = None
    ff_log_path = None
    w = h = None
    n = 0
    enhanced = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            n += 1
            if n % 25 == 0:
                log("progress:", n, "/", total, "frame (enhanced=%d)" % enhanced)
            out_frame = frame
            try:
                _, _, restored = restorer.enhance(
                    frame,
                    has_aligned=False,
                    only_center_face=bool(args.only_center_face),
                    paste_back=True,
                )
                if restored is not None:
                    if restored.shape[:2] != frame.shape[:2]:
                        restored = cv2.resize(
                            restored, (frame.shape[1], frame.shape[0]),
                            interpolation=cv2.INTER_LANCZOS4)
                    if strength >= 1.0:
                        out_frame = restored
                    else:
                        out_frame = cv2.addWeighted(
                            restored, strength, frame, 1.0 - strength, 0)
                    enhanced += 1
            except Exception as e:
                if n <= 3:
                    log("frame", n, "enhance gagal, pakai asli:", e)
                out_frame = frame

            if ff is None:
                h, w = out_frame.shape[:2]
                ff_log_path = args.output + ".fflog.txt"
                try:
                    ff_log = open(ff_log_path, "wb")
                except Exception:
                    ff_log = subprocess.DEVNULL
                cmd = [
                    "ffmpeg", "-y", "-loglevel", "error", "-nostats",
                    "-f", "rawvideo", "-pix_fmt", "bgr24",
                    "-s", str(w) + "x" + str(h), "-r", str(fps), "-i", "pipe:0",
                    "-i", args.input,
                    "-map", "0:v:0", "-map", "1:a:0?",
                    "-c:v", "libx264", "-preset", "medium", "-crf", str(args.crf),
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                    "-movflags", "+faststart", "-shortest", args.output,
                ]
                # stderr -> file log (BUKAN pipe) supaya tidak deadlock.
                ff = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                      stdout=subprocess.DEVNULL,
                                      stderr=ff_log)
            else:
                if out_frame.shape[0] != h or out_frame.shape[1] != w:
                    out_frame = cv2.resize(out_frame, (w, h))
            try:
                ff.stdin.write(out_frame.tobytes())
            except (BrokenPipeError, OSError) as e:
                log("ffmpeg menutup pipe lebih awal:", e)
                break
    finally:
        cap.release()

    def _read_fflog():
        try:
            if ff_log_path and os.path.isfile(ff_log_path):
                with open(ff_log_path, "r", errors="ignore") as f:
                    return f.read()[-500:]
        except Exception:
            pass
        return ""

    def _close_fflog():
        try:
            if ff_log and ff_log is not subprocess.DEVNULL:
                ff_log.close()
        except Exception:
            pass

    if ff is None or n == 0:
        _close_fflog()
        log("tidak ada frame terbaca / ffmpeg tidak start")
        return 8
    if enhanced == 0:
        try:
            ff.stdin.close()
            ff.wait(timeout=60)
        except Exception:
            pass
        _close_fflog()
        try:
            if os.path.isfile(args.output):
                os.remove(args.output)
        except Exception:
            pass
        log("tidak ada wajah yang berhasil di-enhance (0 frame). Batalkan.")
        return 9
    try:
        ff.stdin.close()
    except Exception:
        pass
    ret = ff.wait()
    _close_fflog()
    if ret != 0:
        log("ffmpeg gagal (rc %s):" % ret, _read_fflog())
        return 10
    try:
        if ff_log_path and os.path.isfile(ff_log_path):
            os.remove(ff_log_path)
    except Exception:
        pass
    log("selesai:", args.output, "frame:", n, "enhanced:", enhanced)
    return 0


if __name__ == "__main__":
    sys.exit(main())
