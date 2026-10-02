"""
Mesin lip-sync OtoPost dengan PILIHAN BACKEND (Wav2Lip / MuseTalk) + enhancer GFPGAN.

PILIH BACKEND lewat env LIPSYNC_BACKEND:
- "wav2lip" (DEFAULT) : cepat, ringan, tapi mulut 96x96 (buram, cenderung buka-tutup).
- "musetalk"          : MuseTalk 1.5, wajah 256x256 di latent space -> jauh lebih
                        membentuk kata, tajam, minim glitch. Butuh clone MuseTalk +
                        weights (lihat musetalk_engine.py). Cocok RTX 4060 (fp16).

Apapun backend-nya, /lipsync SELALU fail-safe: bila backend belum siap / gagal,
otomatis fallback ke overlay suara (tetap menghasilkan video dgn suara baru).
Hasil kedua backend bisa DIPERHALUS lagi oleh GFPGAN (ENABLE_ENHANCE, langkah kedua).

Gunakan status() / endpoint GET /lipsync-status untuk mendiagnosis apa yang kurang.

===================== BACKEND WAV2LIP =====================
Aktif (sebagai backend wav2lip) hanya bila SEMUA terpenuhi:
- ENABLE_WAV2LIP = 1 (atau true/yes/on)
- WAV2LIP_DIR    = path folder clone repo Wav2Lip (berisi inference.py)
- WAV2LIP_CKPT   = path checkpoint (mis. .../checkpoints/wav2lip_gan.pth)
- python (WAV2LIP_PYTHON, default "python") punya PyTorch terpasang

KONFIGURASI PERFORMA (semua ADA DEFAULT aman untuk RTX 4060 / VRAM 8GB).
Ubah lewat environment variable SEBELUM start server:
- WAV2LIP_RESIZE_FACTOR       (default 1)   downscale internal Wav2Lip. Naikkan ke 2/3 bila
                                            muncul "Image too big" / CUDA out of memory.
- WAV2LIP_FACE_DET_BATCH_SIZE (default 4)   batch deteksi wajah. Kecil = hemat VRAM.
- WAV2LIP_BATCH_SIZE          (default 64)  batch Wav2Lip. Kecil = hemat VRAM.
- WAV2LIP_PADS                (default "0 10 0 0")  padding kotak wajah (atas bawah kiri kanan).
- WAV2LIP_MAX_FACE_HEIGHT     (default 1280) tinggi maksimum frame wajah yang dikirim ke Wav2Lip
                                            (remake.py yang menerapkan). Kecil = jauh lebih hemat VRAM.
- WAV2LIP_TIMEOUT_SEC         (default 900) batas waktu 1 proses Wav2Lip (detik).
- WAV2LIP_EXTRA_ARGS          (opsional) argumen tambahan mentah; menimpa default di atas bila
                                            flag yang sama disebut.

KUALITAS / NATURAL (diteruskan ke inference.py; semua ADA DEFAULT):
- WAV2LIP_BLEND           (default 1)     1=blend mulut halus (hilangkan kotak), 0=tempel kasar.
- WAV2LIP_FEATHER         (default 0.12)  lebar tepi transisi (fraksi kotak wajah).
- WAV2LIP_MOUTH_TOP       (default 0.45)  fraksi bagian atas wajah yang dipertahankan asli.
- WAV2LIP_SEAMLESS        (default 0)     1=Poisson seamlessClone (paling menyatu, lebih berat).
- WAV2LIP_COLOR_MATCH     (default 1)     1=samakan warna tempelan dgn kulit sekitar.
- WAV2LIP_FREEZE_SILENCE  (default 1)     1=mulut diam saat audio hening (jeda napas/kalimat).
- WAV2LIP_SILENCE_DB      (default -38)   ambang dB hening.
- WAV2LIP_MIN_SILENCE_MS  (default 120)   durasi minimum hening (ms) agar mulut dibekukan.

===================== BACKEND MUSETALK =====================
Setel LIPSYNC_BACKEND=musetalk lalu isi MUSETALK_DIR (+ weights). Semua knob ada di
musetalk_engine.py (MUSETALK_PYTHON, MUSETALK_FP16, MUSETALK_BATCH_SIZE, dst).

===================== FACE ENHANCER (GFPGAN) =====================
LANGKAH KEDUA pasca lipsync (berlaku untuk KEDUA backend; semua ADA DEFAULT).
Merestorasi wajah tiap frame agar mulut lebih tajam & menyatu. Butuh enhance.py +
model GFPGANv1.4.pth. Bila belum ada, langkah ini otomatis DILEWATI (aman).
- ENABLE_ENHANCE           (default 1)   1=perhalus hasil dgn GFPGAN bila siap.
- ENHANCE_SCRIPT           (default <WAV2LIP_DIR>/enhance.py)  path skrip enhance.py.
- GFPGAN_MODEL             (default <WAV2LIP_DIR>/gfpgan/GFPGANv1.4.pth)  path model GFPGAN.
- ENHANCE_PYTHON           (default WAV2LIP_PYTHON)  python venv yang punya gfpgan+torch.
- ENHANCE_STRENGTH         (default 0.8) 0..1 kekuatan blend hasil restorasi (kecil=lebih natural).
- ENHANCE_UPSCALE          (default 1)   faktor upscale GFPGAN.
- ENHANCE_ONLY_CENTER_FACE (default 1)   1=hanya wajah tengah (hindari flicker wajah lain).
- ENHANCE_CRF              (default 18)  kualitas encode akhir (kecil=lebih tajam/berat).
- ENHANCE_TIMEOUT_SEC      (default 1200) batas waktu proses enhancer.

GPU di-serialkan: hanya 1 proses (lipsync ATAU enhancer) berjalan pada satu waktu
(mencegah dua job rebutan VRAM -> CUDA out of memory). Job berikutnya otomatis mengantre.
Aman berbagi GPU dengan proses lain (mis. camerad/NLU/SBERT) karena akses di-serialkan.
"""
import os
import subprocess
import threading

import musetalk_engine

# Kunci global GPU: cegah 2 proses berat jalan bersamaan (penyebab utama CUDA OOM).
_GPU_LOCK = threading.Lock()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    try:
        v = str(os.environ.get(name, "")).strip()
        return int(v) if v else default
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        v = str(os.environ.get(name, "")).strip()
        return float(v) if v else default
    except Exception:
        return default


def backend() -> str:
    """Backend lipsync aktif: "wav2lip" (default) atau "musetalk"."""
    b = _env("LIPSYNC_BACKEND", "wav2lip").strip().lower()
    return b if b in ("wav2lip", "musetalk") else "wav2lip"


def enabled() -> bool:
    return _env("ENABLE_WAV2LIP", "").lower() in ("1", "true", "yes", "on")


def wav2lip_ready() -> bool:
    """Kesiapan khusus backend Wav2Lip (tanpa melihat LIPSYNC_BACKEND)."""
    if not enabled():
        return False
    wav2lip_dir = _env("WAV2LIP_DIR")
    ckpt = _env("WAV2LIP_CKPT")
    if not wav2lip_dir or not os.path.isdir(wav2lip_dir):
        return False
    if not ckpt or not os.path.exists(ckpt):
        return False
    return os.path.exists(os.path.join(wav2lip_dir, "inference.py"))


def is_ready() -> bool:
    """Kesiapan backend AKTIF (dipakai remake.py untuk memutuskan TRUE lipsync)."""
    if backend() == "musetalk":
        return musetalk_engine.ready()
    return wav2lip_ready()


def enhance_enabled() -> bool:
    return _env("ENABLE_ENHANCE", "1").lower() in ("1", "true", "yes", "on")


def _enhance_script() -> str:
    s = _env("ENHANCE_SCRIPT")
    if s:
        return s
    wd = _env("WAV2LIP_DIR")
    return os.path.join(wd, "enhance.py") if wd else "enhance.py"


def _enhance_model() -> str:
    m = _env("GFPGAN_MODEL")
    if m:
        return m
    wd = _env("WAV2LIP_DIR")
    return os.path.join(wd, "gfpgan", "GFPGANv1.4.pth") if wd else ""


def _enhance_python() -> str:
    return _env("ENHANCE_PYTHON") or _env("WAV2LIP_PYTHON", "python")


def enhance_ready() -> bool:
    if not enhance_enabled():
        return False
    return os.path.isfile(_enhance_script()) and os.path.isfile(_enhance_model())


def config() -> dict:
    """Konfigurasi performa aktif + default-nya (dipakai juga oleh /lipsync-status)."""
    return {
        "resizeFactor": _env_int("WAV2LIP_RESIZE_FACTOR", 1),
        "faceDetBatchSize": _env_int("WAV2LIP_FACE_DET_BATCH_SIZE", 8),
        "wav2lipBatchSize": _env_int("WAV2LIP_BATCH_SIZE", 128),
        "pads": _env("WAV2LIP_PADS", "0 10 0 0"),
        "maxFaceHeight": _env_int("WAV2LIP_MAX_FACE_HEIGHT", 960),
        "timeoutSec": _env_int("WAV2LIP_TIMEOUT_SEC", 900),
        # --- Kualitas / naturalisasi (diteruskan ke inference.py) ---
        "blend": _env_int("WAV2LIP_BLEND", 1),
        "feather": _env_float("WAV2LIP_FEATHER", 0.12),
        "mouthTop": _env_float("WAV2LIP_MOUTH_TOP", 0.45),
        "seamless": _env_int("WAV2LIP_SEAMLESS", 0),
        "colorMatch": _env_int("WAV2LIP_COLOR_MATCH", 1),
        "freezeSilence": _env_int("WAV2LIP_FREEZE_SILENCE", 1),
        "silenceDb": _env_float("WAV2LIP_SILENCE_DB", -38.0),
        "minSilenceMs": _env_int("WAV2LIP_MIN_SILENCE_MS", 120),
        # --- Face enhancer GFPGAN (langkah kedua) ---
        "enhanceEnabled": 1 if enhance_enabled() else 0,
        "enhanceScript": _enhance_script(),
        "gfpganModel": _enhance_model(),
        "enhancePython": _enhance_python(),
        "enhanceStrength": _env_float("ENHANCE_STRENGTH", 0.8),
        "enhanceUpscale": _env_int("ENHANCE_UPSCALE", 1),
        "enhanceOnlyCenterFace": _env_int("ENHANCE_ONLY_CENTER_FACE", 1),
        "enhanceCrf": _env_int("ENHANCE_CRF", 18),
        "enhanceReady": enhance_ready(),
        "extraArgs": _env("WAV2LIP_EXTRA_ARGS", ""),
    }


def max_face_height() -> int:
    return _env_int("WAV2LIP_MAX_FACE_HEIGHT", 960)


def _torch_check(python_bin: str) -> dict:
    try:
        out = subprocess.run(
            [python_bin, "-c",
             "import torch; print(torch.__version__); print(torch.cuda.is_available())"],
            capture_output=True, timeout=90,
        )
        if out.returncode != 0:
            return {"importOk": False, "error": out.stderr.decode(errors="ignore")[:300]}
        lines = out.stdout.decode(errors="ignore").strip().splitlines()
        version = lines[0].strip() if len(lines) > 0 else "?"
        cuda = (lines[1].strip() == "True") if len(lines) > 1 else False
        return {"importOk": True, "version": version, "cudaAvailable": cuda}
    except Exception as e:
        return {"importOk": False, "error": str(e)[:300]}


def status() -> dict:
    wav2lip_dir = _env("WAV2LIP_DIR")
    ckpt = _env("WAV2LIP_CKPT")
    python_bin = _env("WAV2LIP_PYTHON", "python")
    inference = os.path.join(wav2lip_dir, "inference.py") if wav2lip_dir else ""
    active = backend()
    st = {
        "backend": active,
        "enabled": enabled(),
        "wav2lipDir": wav2lip_dir,
        "wav2lipDirExists": bool(wav2lip_dir) and os.path.isdir(wav2lip_dir),
        "inferenceExists": bool(inference) and os.path.exists(inference),
        "checkpoint": ckpt,
        "checkpointExists": bool(ckpt) and os.path.exists(ckpt),
        "pythonBin": python_bin,
        "extraArgs": _env("WAV2LIP_EXTRA_ARGS", ""),
        "config": config(),
        "wav2lipReady": wav2lip_ready(),
        "musetalk": musetalk_engine.status(),
        "ready": is_ready(),
        "enhanceReady": enhance_ready(),
    }
    st["torch"] = _torch_check(python_bin)
    # Ringkasan tindakan bila belum siap
    hints = []
    if active == "musetalk":
        if not musetalk_engine.ready():
            hints += musetalk_engine.status().get("hints", [])
            hints.append("Set MUSETALK_DIR + weights, atau ganti LIPSYNC_BACKEND=wav2lip.")
        mt_py = musetalk_engine.status().get("python", "python")
        mt_torch = _torch_check(mt_py)
        st["musetalkTorch"] = mt_torch
        if not mt_torch.get("importOk"):
            hints.append("MUSETALK_PYTHON tidak bisa import torch. Arahkan ke venv MuseTalk.")
        elif not mt_torch.get("cudaAvailable"):
            hints.append("CUDA tak terdeteksi di venv MuseTalk -> lambat (CPU).")
    else:
        if not st["enabled"]:
            hints.append("Set ENABLE_WAV2LIP=1 SEBELUM start server (di proses yang sama).")
        if not st["wav2lipDirExists"]:
            hints.append("WAV2LIP_DIR belum menunjuk ke folder clone Wav2Lip yang valid.")
        elif not st["inferenceExists"]:
            hints.append("inference.py tidak ada di WAV2LIP_DIR.")
        if not st["checkpointExists"]:
            hints.append("WAV2LIP_CKPT (wav2lip_gan.pth) tidak ditemukan.")
        if not st["torch"].get("importOk"):
            hints.append("PyTorch tidak bisa di-import oleh pythonBin. Set WAV2LIP_PYTHON ke python venv yang ada torch.")
        elif not st["torch"].get("cudaAvailable"):
            hints.append("CUDA tidak terdeteksi -> jalan di CPU (lambat). Install torch build cu121 untuk pakai RTX 4060.")
    # Enhancer (GFPGAN)
    cfg = st["config"]
    if cfg.get("enhanceEnabled") and not cfg.get("enhanceReady"):
        if not os.path.isfile(cfg.get("enhanceScript", "")):
            hints.append("Enhancer aktif tapi enhance.py tidak ada di %s (salin enhance.py ke sana)." % cfg.get("enhanceScript"))
        if not os.path.isfile(cfg.get("gfpganModel", "")):
            hints.append("Enhancer aktif tapi model GFPGAN tidak ada di %s (unduh GFPGANv1.4.pth)." % cfg.get("gfpganModel"))
    st["hints"] = hints
    return st


def _build_perf_args(cfg: dict) -> list:
    """Susun argumen performa default, lewati flag yang sudah ada di WAV2LIP_EXTRA_ARGS."""
    extra = (cfg.get("extraArgs") or "").strip()
    present = set(tok for tok in extra.split() if tok.startswith("--"))
    args = []
    if "--resize_factor" not in present:
        args += ["--resize_factor", str(max(1, int(cfg["resizeFactor"])))]
    if "--face_det_batch_size" not in present:
        args += ["--face_det_batch_size", str(max(1, int(cfg["faceDetBatchSize"])))]
    if "--wav2lip_batch_size" not in present:
        args += ["--wav2lip_batch_size", str(max(1, int(cfg["wav2lipBatchSize"])))]
    if "--pads" not in present and str(cfg.get("pads", "")).strip():
        args += ["--pads"] + str(cfg["pads"]).split()
    # --- Flag kualitas / naturalisasi ---
    if "--blend" not in present:
        args += ["--blend", str(int(cfg["blend"]))]
    if "--feather" not in present:
        args += ["--feather", str(float(cfg["feather"]))]
    if "--mouth_top" not in present:
        args += ["--mouth_top", str(float(cfg["mouthTop"]))]
    if "--seamless" not in present:
        args += ["--seamless", str(int(cfg["seamless"]))]
    if "--color_match" not in present:
        args += ["--color_match", str(int(cfg["colorMatch"]))]
    if "--freeze_silence" not in present:
        args += ["--freeze_silence", str(int(cfg["freezeSilence"]))]
    if "--silence_db" not in present:
        args += ["--silence_db", str(float(cfg["silenceDb"]))]
    if "--min_silence_ms" not in present:
        args += ["--min_silence_ms", str(int(cfg["minSilenceMs"]))]
    if extra:
        args += extra.split()
    return args


def run_enhance(in_path: str, out_path: str) -> bool:
    """Jalankan enhance.py (GFPGAN) untuk mempertajam wajah/mulut. GPU di-serialkan.
    Return True bila output enhanced berhasil dibuat."""
    py = _enhance_python()
    script = _enhance_script()
    model = _enhance_model()
    cmd = [
        py, script,
        "--input", in_path,
        "--output", out_path,
        "--model", model,
        "--strength", str(_env_float("ENHANCE_STRENGTH", 0.8)),
        "--upscale", str(_env_int("ENHANCE_UPSCALE", 1)),
        "--only_center_face", str(_env_int("ENHANCE_ONLY_CENTER_FACE", 1)),
        "--crf", str(_env_int("ENHANCE_CRF", 18)),
    ]
    child_env = os.environ.copy()
    child_env.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    print("Enhancer: menjalankan ->", " ".join(cmd))
    with _GPU_LOCK:
        try:
            proc = subprocess.run(
                cmd, capture_output=True, env=child_env,
                timeout=max(60, _env_int("ENHANCE_TIMEOUT_SEC", 1200)),
            )
        except subprocess.TimeoutExpired:
            print("Enhancer: TIMEOUT.")
            return False
        except Exception as e:
            print("Enhancer: gagal memanggil subprocess:", str(e)[:300])
            return False
    if proc.returncode != 0:
        print("Enhancer: dilewati/gagal (returncode %s):" % proc.returncode,
              proc.stderr.decode(errors="ignore")[-800:])
        return False
    if not os.path.exists(out_path):
        print("Enhancer: selesai tapi output tidak ditemukan:", out_path)
        return False
    return True


def _maybe_enhance(out_path: str) -> None:
    """Langkah kedua opsional: perhalus hasil lipsync dgn GFPGAN bila siap.
    Selalu fail-safe: bila gagal, hasil lipsync apa adanya tetap dipakai."""
    if not enhance_enabled():
        return
    if not enhance_ready():
        print("Enhancer: dilewati (enhance.py / model GFPGAN belum ada). Cek GET /lipsync-status.")
        return
    tmp = out_path + ".enh.mp4"
    if run_enhance(out_path, tmp):
        try:
            os.replace(tmp, out_path)
            print("Enhancer: hasil dipertajam GFPGAN ->", out_path)
        except Exception as e:
            print("Enhancer: gagal menimpa output:", str(e)[:200])
            try:
                if os.path.isfile(tmp):
                    os.remove(tmp)
            except Exception:
                pass
    else:
        print("Enhancer: gagal/dilewati, pakai hasil lipsync apa adanya.")
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except Exception:
            pass


def run_lipsync(face_path: str, audio: str, out_path: str) -> bool:
    """Dispatcher: jalankan backend lipsync AKTIF (wav2lip/musetalk) lalu (opsional)
    perhalus dengan GFPGAN. Return True bila output lipsync berhasil dibuat.
    face_path boleh video ATAU gambar (foto diam)."""
    if backend() == "musetalk":
        ok = musetalk_engine.run(face_path, audio, out_path, gpu_lock=_GPU_LOCK)
        if not ok:
            return False
        # Langkah 2 (opsional): perhalus wajah/mulut dgn GFPGAN (fail-safe).
        _maybe_enhance(out_path)
        return True
    # Default: Wav2Lip (sudah memanggil _maybe_enhance di dalamnya).
    return run_wav2lip(face_path, audio, out_path)


def run_wav2lip(face_path: str, audio: str, out_path: str) -> bool:
    """Jalankan Wav2Lip. face_path boleh berupa video ATAU gambar (foto diam).
    Return True bila output berhasil dibuat."""
    if not wav2lip_ready():
        print("Wav2Lip: BELUM SIAP (enabled=%s, ready=%s). Cek GET /lipsync-status."
              % (enabled(), wav2lip_ready()))
        return False
    wav2lip_dir = _env("WAV2LIP_DIR")
    ckpt = _env("WAV2LIP_CKPT")
    python_bin = _env("WAV2LIP_PYTHON", "python")
    cfg = config()
    cmd = [
        python_bin, os.path.join(wav2lip_dir, "inference.py"),
        "--checkpoint_path", ckpt,
        "--face", face_path,
        "--audio", audio,
        "--outfile", out_path,
    ] + _build_perf_args(cfg)
    # Kurangi fragmentasi VRAM (mengurangi kemungkinan OOM di GPU kecil).
    child_env = os.environ.copy()
    child_env.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    print("Wav2Lip: menjalankan ->", " ".join(cmd))
    # Serialkan GPU: hanya 1 proses pada satu waktu.
    with _GPU_LOCK:
        try:
            proc = subprocess.run(
                cmd, capture_output=True, cwd=wav2lip_dir, env=child_env,
                timeout=max(60, int(cfg["timeoutSec"])),
            )
        except subprocess.TimeoutExpired:
            print("Wav2Lip: TIMEOUT setelah %ss (proses terlalu lama)." % cfg["timeoutSec"])
            return False
        except Exception as e:
            print("Wav2Lip: gagal memanggil subprocess:", str(e)[:300])
            return False
    if proc.returncode != 0:
        print("Wav2Lip ERROR (returncode %s):" % proc.returncode,
              proc.stderr.decode(errors="ignore")[-1500:])
        return False
    if not os.path.exists(out_path):
        print("Wav2Lip: selesai tapi output tidak ditemukan:", out_path)
        return False
    print("Wav2Lip: SUKSES ->", out_path)
    # Langkah 2 (opsional): perhalus wajah/mulut dgn GFPGAN (fail-safe).
    _maybe_enhance(out_path)
    return True
