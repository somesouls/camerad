"""
Backend lip-sync MuseTalk 1.5 (Tencent) untuk OtoPost.

KENAPA MuseTalk (vs Wav2Lip)?
- Wav2Lip meng-generate mulut hanya 96x96 lalu di-upscale -> buram & "buka-tutup"
  mengikuti energi suara, bukan bentuk kata.
- MuseTalk bekerja di latent space (VAE) dgn wajah 256x256 + fitur audio Whisper,
  meng-inpaint mulut sehingga jauh lebih membentuk kata, tajam, minim glitch.
- Real-time & ringan: bisa jalan di VRAM kecil (fp16), cocok untuk RTX 4060 8GB.

DIPANGGIL oleh lipsync_engine.run_lipsync() bila LIPSYNC_BACKEND=musetalk.
Selalu FAIL-SAFE: bila belum siap / error / output tak muncul -> return False,
sehingga /lipsync otomatis fallback (overlay suara) dan pipeline tak pernah rusak.

Memakai skrip resmi MuseTalk mode "normal": `python -m scripts.inference`.
Proses ini butuh CWD = folder MuseTalk (banyak path relatif ./models/...).

KONFIGURASI (environment variable; semua ADA DEFAULT):
- MUSETALK_DIR            (WAJIB) folder clone MuseTalk (berisi scripts/, models/).
- MUSETALK_PYTHON         (default "python") python venv MuseTalk (punya torch, mmcv, dll).
- MUSETALK_VERSION        (default "v15") "v15" atau "v1".
- MUSETALK_UNET           (default <DIR>/models/musetalkV15/unet.pth)
- MUSETALK_UNET_CONFIG    (default <DIR>/models/musetalkV15/musetalk.json)
- MUSETALK_WHISPER_DIR    (default <DIR>/models/whisper)
- MUSETALK_VAE_TYPE       (default "sd-vae")
- MUSETALK_FFMPEG         (opsional) folder bin ffmpeg (Windows). Kosong = pakai PATH.
- MUSETALK_FP16           (default 1) 1=--use_float16 (hemat VRAM, disarankan di 8GB).
- MUSETALK_BATCH_SIZE     (default 8) kecilkan (mis. 4) bila CUDA OOM.
- MUSETALK_FPS            (default 25) dipakai untuk input FOTO (video pakai fps aslinya).
- MUSETALK_EXTRA_MARGIN   (default 10) margin bawah crop wajah (v15).
- MUSETALK_PARSING_MODE   (default "jaw") mode blending face-parsing (v15).
- MUSETALK_LEFT_CHEEK     (default 90) / MUSETALK_RIGHT_CHEEK (default 90)
- MUSETALK_BBOX_SHIFT     (default 0) hanya dipakai v1 (v15 mengabaikan).
- MUSETALK_GPU_ID         (default 0)
- MUSETALK_RESULT_DIR     (default <DIR>/results/otopost) folder output sementara.
- MUSETALK_TIMEOUT_SEC    (default 1800) batas waktu 1 proses.
- MUSETALK_AUDIO_TO_WAV   (default 1) 1=konversi audio ke wav 16k mono dulu (lebih kompatibel).
- MUSETALK_INFERENCE_MODULE (default "scripts.inference")
- MUSETALK_EXTRA_ARGS     (opsional) argumen mentah tambahan.

CATATAN: scripts/inference.py MuseTalk membungkus tiap task dgn try/except dan bisa
keluar returncode 0 walau gagal. Karena itu kita TIDAK mengandalkan returncode saja,
melainkan MEMVERIFIKASI file output benar-benar ada.
"""
import os
import uuid
import shutil
import threading
import subprocess

# Lock GPU lokal (dipakai bila pemanggil tidak menyuplai lock bersama).
_LOCAL_GPU_LOCK = threading.Lock()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    try:
        v = str(os.environ.get(name, "")).strip()
        return int(v) if v else default
    except Exception:
        return default


def _truthy(name: str, default: str = "1") -> bool:
    return _env(name, default).lower() in ("1", "true", "yes", "on")


def _dir() -> str:
    return _env("MUSETALK_DIR").strip()


def _version() -> str:
    v = _env("MUSETALK_VERSION", "v15").strip().lower()
    return v if v in ("v1", "v15") else "v15"


def _python() -> str:
    return _env("MUSETALK_PYTHON", "python")


def _unet() -> str:
    m = _env("MUSETALK_UNET").strip()
    if m:
        return m
    d = _dir()
    sub = "musetalkV15" if _version() == "v15" else "musetalk"
    fname = "unet.pth" if _version() == "v15" else "pytorch_model.bin"
    return os.path.join(d, "models", sub, fname) if d else ""


def _unet_config() -> str:
    c = _env("MUSETALK_UNET_CONFIG").strip()
    if c:
        return c
    d = _dir()
    sub = "musetalkV15" if _version() == "v15" else "musetalk"
    return os.path.join(d, "models", sub, "musetalk.json") if d else ""


def _whisper_dir() -> str:
    w = _env("MUSETALK_WHISPER_DIR").strip()
    if w:
        return w
    d = _dir()
    return os.path.join(d, "models", "whisper") if d else ""


def _result_dir() -> str:
    r = _env("MUSETALK_RESULT_DIR").strip()
    if r:
        return r
    d = _dir()
    return os.path.join(d, "results", "otopost") if d else ""


def _inference_script() -> str:
    d = _dir()
    return os.path.join(d, "scripts", "inference.py") if d else ""


def ready() -> bool:
    """True bila semua berkas inti MuseTalk ada. Tidak mengecek import torch
    (itu dicek saat proses berjalan) supaya cepat & tak butuh dependensi di server."""
    d = _dir()
    if not d or not os.path.isdir(d):
        return False
    if not os.path.isfile(_inference_script()):
        return False
    if not os.path.isfile(_unet()):
        return False
    if not os.path.isfile(_unet_config()):
        return False
    if not os.path.isdir(_whisper_dir()):
        return False
    return True


def status() -> dict:
    d = _dir()
    return {
        "dir": d,
        "dirExists": bool(d) and os.path.isdir(d),
        "version": _version(),
        "python": _python(),
        "inferenceScript": _inference_script(),
        "inferenceScriptExists": os.path.isfile(_inference_script()),
        "unet": _unet(),
        "unetExists": os.path.isfile(_unet()),
        "unetConfig": _unet_config(),
        "unetConfigExists": os.path.isfile(_unet_config()),
        "whisperDir": _whisper_dir(),
        "whisperDirExists": os.path.isdir(_whisper_dir()),
        "resultDir": _result_dir(),
        "fp16": _truthy("MUSETALK_FP16", "1"),
        "batchSize": _env_int("MUSETALK_BATCH_SIZE", 8),
        "fps": _env_int("MUSETALK_FPS", 25),
        "ffmpegPath": _env("MUSETALK_FFMPEG", ""),
        "timeoutSec": _env_int("MUSETALK_TIMEOUT_SEC", 1800),
        "ready": ready(),
        "hints": _hints(),
    }


def _hints() -> list:
    h = []
    d = _dir()
    if not d or not os.path.isdir(d):
        h.append("MUSETALK_DIR belum menunjuk folder clone MuseTalk yang valid.")
        return h
    if not os.path.isfile(_inference_script()):
        h.append("scripts/inference.py tidak ada di MUSETALK_DIR.")
    if not os.path.isfile(_unet()):
        h.append("Bobot UNet tidak ada: %s (unduh weights MuseTalk)." % _unet())
    if not os.path.isfile(_unet_config()):
        h.append("Config UNet tidak ada: %s" % _unet_config())
    if not os.path.isdir(_whisper_dir()):
        h.append("Folder whisper tidak ada: %s (unduh whisper-tiny)." % _whisper_dir())
    return h


def _norm(p: str) -> str:
    """Normalkan ke forward-slash agar aman ditulis di YAML & lintas-OS."""
    return os.path.abspath(p).replace("\\", "/")


def _to_wav(audio: str, dst: str, timeout: int) -> bool:
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", audio,
           "-ac", "1", "-ar", "16000", dst]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except Exception as e:
        print("MuseTalk: gagal konversi audio->wav:", str(e)[:200])
        return False
    if p.returncode != 0 or not os.path.isfile(dst):
        print("MuseTalk: ffmpeg audio->wav gagal:",
              p.stderr.decode(errors="ignore")[-300:])
        return False
    return True


def _find_latest_mp4(folder: str) -> str:
    try:
        if not os.path.isdir(folder):
            return ""
        cands = [os.path.join(folder, f) for f in os.listdir(folder)
                 if f.lower().endswith(".mp4")]
        if not cands:
            return ""
        cands.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        return cands[0]
    except Exception:
        return ""


def _cleanup(paths) -> None:
    for p in paths:
        if not p:
            continue
        try:
            if os.path.isfile(p):
                os.remove(p)
        except Exception:
            pass


def run(face_path: str, audio: str, out_path: str, gpu_lock=None) -> bool:
    """Jalankan MuseTalk untuk menghasilkan video lipsync di out_path.
    face_path boleh video ATAU gambar (foto diam). Return True bila sukses."""
    if not ready():
        print("MuseTalk: BELUM SIAP. Hints:", "; ".join(_hints()))
        return False

    d = _dir()
    version = _version()
    result_dir = _result_dir()
    os.makedirs(result_dir, exist_ok=True)

    # Audio: konversi ke wav 16k mono utk kompatibilitas maksimum (opsional).
    audio_use = audio
    tmp_wav = ""
    timeout = max(120, _env_int("MUSETALK_TIMEOUT_SEC", 1800))
    if _truthy("MUSETALK_AUDIO_TO_WAV", "1"):
        tmp_wav = out_path + ".mtaudio.wav"
        if _to_wav(audio, tmp_wav, timeout):
            audio_use = tmp_wav
        else:
            tmp_wav = ""  # gagal -> pakai audio asli

    # Nama hasil unik + tulis config YAML sementara.
    result_name = "otopost_" + uuid.uuid4().hex[:12] + ".mp4"
    cfg_path = os.path.join(result_dir, "cfg_" + uuid.uuid4().hex[:8] + ".yaml")
    yaml_text = (
        "task_0:\n"
        ' video_path: "' + _norm(face_path) + '"\n'
        ' audio_path: "' + _norm(audio_use) + '"\n'
        ' result_name: "' + result_name + '"\n'
    )
    try:
        with open(cfg_path, "w", encoding="utf-8") as f:
            f.write(yaml_text)
    except Exception as e:
        print("MuseTalk: gagal tulis config:", str(e)[:200])
        _cleanup([tmp_wav])
        return False

    cmd = [
        _python(), "-m", _env("MUSETALK_INFERENCE_MODULE", "scripts.inference"),
        "--inference_config", cfg_path,
        "--result_dir", result_dir,
        "--unet_model_path", _unet(),
        "--unet_config", _unet_config(),
        "--whisper_dir", _whisper_dir(),
        "--vae_type", _env("MUSETALK_VAE_TYPE", "sd-vae"),
        "--version", version,
        "--gpu_id", str(_env_int("MUSETALK_GPU_ID", 0)),
        "--batch_size", str(max(1, _env_int("MUSETALK_BATCH_SIZE", 8))),
        "--fps", str(_env_int("MUSETALK_FPS", 25)),
        "--extra_margin", str(_env_int("MUSETALK_EXTRA_MARGIN", 10)),
        "--parsing_mode", _env("MUSETALK_PARSING_MODE", "jaw"),
        "--left_cheek_width", str(_env_int("MUSETALK_LEFT_CHEEK", 90)),
        "--right_cheek_width", str(_env_int("MUSETALK_RIGHT_CHEEK", 90)),
    ]
    if version == "v1":
        cmd += ["--bbox_shift", str(_env_int("MUSETALK_BBOX_SHIFT", 0))]
    if _truthy("MUSETALK_FP16", "1"):
        cmd += ["--use_float16"]
    ffmpeg_path = _env("MUSETALK_FFMPEG", "").strip()
    if ffmpeg_path:
        cmd += ["--ffmpeg_path", ffmpeg_path]
    extra = _env("MUSETALK_EXTRA_ARGS", "").strip()
    if extra:
        cmd += extra.split()

    child_env = os.environ.copy()
    child_env.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

    print("MuseTalk: menjalankan ->", " ".join(cmd))
    lock = gpu_lock if gpu_lock is not None else _LOCAL_GPU_LOCK
    with lock:
        try:
            proc = subprocess.run(
                cmd, capture_output=True, cwd=d, env=child_env, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            print("MuseTalk: TIMEOUT setelah %ss." % timeout)
            _cleanup([cfg_path, tmp_wav])
            return False
        except Exception as e:
            print("MuseTalk: gagal memanggil subprocess:", str(e)[:300])
            _cleanup([cfg_path, tmp_wav])
            return False

    tail = proc.stdout.decode(errors="ignore")[-800:] + "\n" + \
        proc.stderr.decode(errors="ignore")[-1200:]
    # Output resmi: <result_dir>/<version>/<result_name>
    produced = os.path.join(result_dir, version, result_name)
    if not os.path.isfile(produced):
        # cadangan: cari mp4 terbaru di folder versi (jaga-jaga penamaan beda).
        produced = _find_latest_mp4(os.path.join(result_dir, version))
    if not produced or not os.path.isfile(produced):
        print("MuseTalk: output tidak ditemukan (rc=%s). Log:" % proc.returncode, tail[-800:])
        _cleanup([cfg_path, tmp_wav])
        return False

    try:
        shutil.move(produced, out_path)
    except Exception as e:
        print("MuseTalk: gagal memindahkan output:", str(e)[:200])
        _cleanup([cfg_path, tmp_wav])
        return False

    _cleanup([cfg_path, tmp_wav])
    print("MuseTalk: SUKSES ->", out_path)
    return True
