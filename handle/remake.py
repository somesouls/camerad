"""
OtoPost Remake / Lipsync endpoints (modul terpisah agar main.py yang sudah jalan aman).

- POST /upload            : upload media (video/photo/audio) -> {mediaId, url}
- POST /lipsync           : MULAI job remake (async). Balas cepat {job, status:"processing", statusUrl}.
- GET  /lipsync-result/{job} : cek status job -> {status: processing|done|error, downloadUrl?, reason?}
- GET  /lipsync-status    : diagnostik apakah Wav2Lip (TRUE lipsync) siap + konfigurasi performa.

KENAPA ASYNC?
Proses Wav2Lip bisa >100 detik, sedangkan proxy (mis. Cloudflare) memutus koneksi ~100s (error 524).
Dengan async, /lipsync langsung balas job id, lalu app polling GET /lipsync-result/{job}.
Setiap request jadi pendek -> tidak kena 524. Server juga tidak memproses dobel karena user retry.

Aturan durasi (SUARA = acuan utama):
- Foto  -> dijadikan video sepanjang audio
- Video lebih pendek dari audio -> di-loop sampai audio selesai
- Video lebih panjang dari audio -> dipotong mengikuti panjang audio

TRUE lipsync (gerak bibir mengikuti kata baru) untuk FOTO maupun VIDEO aktif hanya
bila backend lipsync siap (Wav2Lip: ENABLE_WAV2LIP=1 + WAV2LIP_DIR + WAV2LIP_CKPT + torch;
atau MuseTalk: LIPSYNC_BACKEND=musetalk + MUSETALK_DIR + weights). Bila tidak,
otomatis fallback: foto -> Ken Burns + suara, video -> overlay suara (bibir lama).

EFISIENSI GPU: frame wajah yang dikirim ke Wav2Lip dibatasi tingginya
(WAV2LIP_MAX_FACE_HEIGHT, default 960 = sweet-spot kualitas Wav2Lip) agar tidak
"Image too big" / CUDA OOM di GPU 8GB, sekaligus mulut tidak terlalu blur.

KUALITAS/NATURAL (hilangkan kotak bibir, blend halus, color-match, bekukan mulut saat
hening) diatur di lipsync_engine.py + inference.py. Lihat GET /lipsync-status -> "config".
"""
import os
import json
import uuid
import glob
import shutil
import threading
import subprocess
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, UploadFile, File, Form
from pydantic import BaseModel

import lipsync_engine

WORK_DIR = os.environ.get("CLIP_WORK_DIR", "/data/clips")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
CLIP_SERVER_TOKEN = os.environ.get("CLIP_SERVER_TOKEN", "")
MEDIA_DIR = os.path.join(WORK_DIR, "media")
os.makedirs(MEDIA_DIR, exist_ok=True)

router = APIRouter()

ALLOWED = {
    "video": [".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"],
    "photo": [".jpg", ".jpeg", ".png", ".webp", ".bmp"],
    "audio": [".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus"],
}

# ---- Registry job async (memori + status.json di folder job agar tahan restart) ----
_JOBS = {}
_JOBS_LOCK = threading.Lock()


def _check_auth(authorization: Optional[str]):
    if not CLIP_SERVER_TOKEN:
        return
    if authorization != ("Bearer " + CLIP_SERVER_TOKEN):
        raise HTTPException(status_code=401, detail="Token tidak valid")


def _file_url(rel_path: str) -> str:
    return (PUBLIC_BASE_URL if PUBLIC_BASE_URL else "") + "/files/" + rel_path


def _status_url(job: str) -> str:
    return (PUBLIC_BASE_URL if PUBLIC_BASE_URL else "") + "/lipsync-result/" + job


def _media_path(media_id: str) -> Optional[str]:
    if not media_id:
        return None
    cands = glob.glob(os.path.join(MEDIA_DIR, media_id + ".*"))
    return cands[0] if cands else None


def _ffprobe_duration(path: str) -> float:
    try:
        out = subprocess.check_output([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", path,
        ]).decode().strip()
        return float(out)
    except Exception:
        return 0.0


def _target_scale(aspect: str) -> str:
    if aspect == "1:1":
        return "1080:1080"
    if aspect == "16:9":
        return "1920:1080"
    return "1080:1920"  # 9:16 default


def _face_scale(aspect: str) -> str:
    """Resolusi frame wajah untuk Wav2Lip, dibatasi WAV2LIP_MAX_FACE_HEIGHT agar muat GPU.
    Contoh 9:16 + maxH 960 -> 540:960 (sweet-spot: ringan + mulut tetap tajam)."""
    maxh = max(256, int(lipsync_engine.max_face_height()))
    if aspect == "1:1":
        bw, bh = 1080, 1080
    elif aspect == "16:9":
        bw, bh = 1920, 1080
    else:
        bw, bh = 1080, 1920
    # batasi sisi terpanjang ke maxh (biar 16:9 pun ikut turun)
    longest = max(bw, bh)
    if longest > maxh:
        ratio = maxh / float(longest)
        bw = int(round(bw * ratio / 2) * 2)
        bh = int(round(bh * ratio / 2) * 2)
    return str(max(2, bw)) + ":" + str(max(2, bh))


# ---------------- Upload ----------------
@router.post("/upload")
async def upload(
    kind: str = Form(...),
    file: UploadFile = File(...),
    authorization: Optional[str] = Header(default=None),
):
    _check_auth(authorization)
    if kind not in ALLOWED:
        raise HTTPException(status_code=400, detail="kind harus video/photo/audio")
    _, ext = os.path.splitext(file.filename or "")
    ext = ext.lower()
    if ext not in ALLOWED[kind]:
        ext = ALLOWED[kind][0]
    media_id = kind + "_" + uuid.uuid4().hex[:12]
    dest = os.path.join(MEDIA_DIR, media_id + ext)
    try:
        with open(dest, "wb") as out:
            shutil.copyfileobj(file.file, out)
    finally:
        file.file.close()
    if not os.path.exists(dest) or os.path.getsize(dest) == 0:
        raise HTTPException(status_code=500, detail="Gagal menyimpan file")
    return {"mediaId": media_id, "url": _file_url("media/" + media_id + ext)}


# ---------------- Diagnostik ----------------
@router.get("/lipsync-status")
def lipsync_status():
    """Tampilkan apakah TRUE lipsync (Wav2Lip) siap + konfigurasi performa + petunjuk."""
    return lipsync_engine.status()


# ---------------- Lipsync / Remake ----------------
class LipsyncRequest(BaseModel):
    mediaId: str
    mediaKind: str  # "video" | "photo"
    audioId: str
    mode: Optional[str] = "lipsync"   # "lipsync" | "overlay"
    aspectRatio: Optional[str] = "9:16"
    subtitle: Optional[bool] = False
    subtitleStyle: Optional[str] = "clean"


def _photo_to_still(photo: str, scale: str, out_path: str) -> bool:
    """Scale + crop foto ke rasio target -> 1 gambar diam (input wajah Wav2Lip)."""
    w, h = scale.split(":")
    vf = (
        "scale=" + w + ":" + h + ":force_original_aspect_ratio=increase,"
        "crop=" + w + ":" + h
    )
    cmd = ["ffmpeg", "-y", "-i", photo, "-vf", vf, "-frames:v", "1", out_path]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        print("WARNING: photo_to_still gagal:", proc.stderr.decode(errors="ignore")[:400])
    return proc.returncode == 0 and os.path.exists(out_path)


def _photo_to_video(photo: str, audio: str, dur: float, scale: str, out_path: str) -> bool:
    w, h = scale.split(":")
    frames = max(1, int(round(dur * 30)))
    vf = (
        "scale=" + w + ":" + h + ":force_original_aspect_ratio=increase,"
        "crop=" + w + ":" + h + ","
        "zoompan=z='min(zoom+0.0005,1.15)':d=" + str(frames) + ":s=" + w + "x" + h + ":fps=30,"
        "format=yuv420p"
    )
    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-i", photo, "-i", audio,
        "-vf", vf, "-t", str(dur),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", out_path,
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        print("WARNING: photo_to_video ffmpeg gagal:", proc.stderr.decode(errors="ignore")[:400])
    return proc.returncode == 0 and os.path.exists(out_path)


def _video_remux(video: str, audio: str, adur: float, vdur: float, scale: str, out_path: str) -> bool:
    w, h = scale.split(":")
    vf = (
        "scale=" + w + ":" + h + ":force_original_aspect_ratio=increase,"
        "crop=" + w + ":" + h + ",setsar=1,format=yuv420p"
    )
    cmd = ["ffmpeg", "-y"]
    if vdur > 0 and vdur < adur:
        cmd += ["-stream_loop", "-1", "-i", video]
    else:
        cmd += ["-i", video]
    cmd += [
        "-i", audio, "-vf", vf, "-t", str(adur),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", out_path,
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        print("WARNING: video_remux ffmpeg gagal:", proc.stderr.decode(errors="ignore")[:400])
    return proc.returncode == 0 and os.path.exists(out_path)


def _scale_to_aspect(video: str, scale: str, out_path: str) -> bool:
    """Skala + crop video ke rasio target (silent) untuk input wajah Wav2Lip."""
    w, h = scale.split(":")
    vf = (
        "scale=" + w + ":" + h + ":force_original_aspect_ratio=increase,"
        "crop=" + w + ":" + h + ",setsar=1,format=yuv420p"
    )
    # crf rendah + preset medium: input wajah setajam mungkin (silent) -> lipsync lebih detail.
    cmd = [
        "ffmpeg", "-y", "-i", video, "-an", "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", "16", out_path,
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        print("WARNING: scale_to_aspect gagal:", proc.stderr.decode(errors="ignore")[:400])
    return proc.returncode == 0 and os.path.exists(out_path)


# ---------------- Job registry helpers ----------------
def _job_dir(job: str) -> str:
    return os.path.join(WORK_DIR, job)


def _set_job(job: str, **fields):
    with _JOBS_LOCK:
        st = _JOBS.get(job, {})
        st.update(fields)
        _JOBS[job] = st
        snapshot = dict(st)
    try:
        with open(os.path.join(_job_dir(job), "status.json"), "w", encoding="utf-8") as f:
            json.dump(snapshot, f)
    except Exception:
        pass


def _get_job(job: str) -> Optional[dict]:
    with _JOBS_LOCK:
        if job in _JOBS:
            return dict(_JOBS[job])
    p = os.path.join(_job_dir(job), "status.json")
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def _process_remake(job, media, audio, kind, want_lipsync, scale, face_scale, adur):
    """Pekerjaan berat, dijalankan di thread background."""
    tmp = _job_dir(job)
    out_name = "remake.mp4"
    out_path = os.path.join(tmp, out_name)
    lipsync_applied = False
    reason = ""
    ok = False
    try:
        # ---- Coba TRUE lipsync (foto ATAU video) bila diminta & backend siap ----
        if want_lipsync and lipsync_engine.is_ready():
            try:
                if kind == "photo":
                    face = os.path.join(tmp, "face.png")
                    built = _photo_to_still(media, face_scale, face)
                else:
                    face = os.path.join(tmp, "face.mp4")
                    built = _scale_to_aspect(media, face_scale, face)
                if not built:
                    reason = "Gagal menyiapkan input wajah untuk lipsync (cek ffmpeg)."
                elif lipsync_engine.run_lipsync(face, audio, out_path):
                    lipsync_applied = True
                    ok = True
                else:
                    reason = ("Backend lipsync (%s) tidak menghasilkan output (cek log server / "
                              "wajah tidak terdeteksi / VRAM)." % lipsync_engine.backend())
            except Exception as e:
                reason = "Lipsync error: " + str(e)
                print("WARNING:", reason)
        elif want_lipsync and not lipsync_engine.is_ready():
            reason = ("Backend lipsync (%s) belum siap. Buka GET /lipsync-status untuk detail."
                      % lipsync_engine.backend())

        # ---- Fallback (tanpa gerak bibir) ----
        if not ok:
            if kind == "photo":
                ok = _photo_to_video(media, audio, adur, scale, out_path)
            else:
                vdur = _ffprobe_duration(media)
                ok = _video_remux(media, audio, adur, vdur, scale, out_path)

        if not ok or not os.path.exists(out_path):
            _set_job(job, status="error", reason=(reason or "Gagal memproses remake (ffmpeg)."),
                     lipsyncApplied=False)
            print("Remake GAGAL (%s). Alasan: %s" % (job, reason or "ffmpeg"))
            return

        final_dur = _ffprobe_duration(out_path)
        if lipsync_applied:
            print("Remake selesai: TRUE lipsync diterapkan (%s)." % kind)
        else:
            print("Remake selesai: mode fallback (%s). Alasan: %s" % (kind, reason or "mode overlay diminta"))
        _set_job(
            job,
            status="done",
            downloadUrl=_file_url(job + "/" + out_name),
            durationSec=int(round(final_dur if final_dur > 0 else adur)),
            lipsyncApplied=lipsync_applied,
            reason=reason,
        )
    except Exception as e:
        _set_job(job, status="error", reason="Server error: " + str(e)[:300], lipsyncApplied=False)
        print("Remake EXCEPTION (%s):" % job, str(e)[:300])


@router.post("/lipsync")
def lipsync(req: LipsyncRequest, authorization: Optional[str] = Header(default=None)):
    """Mulai job remake secara async. Balas cepat agar tidak kena timeout proxy (524)."""
    _check_auth(authorization)
    media = _media_path(req.mediaId)
    audio = _media_path(req.audioId)
    if not media:
        raise HTTPException(status_code=404, detail="Media tidak ditemukan: " + str(req.mediaId))
    if not audio:
        raise HTTPException(status_code=404, detail="Audio tidak ditemukan: " + str(req.audioId))
    adur = _ffprobe_duration(audio)
    if adur <= 0:
        raise HTTPException(status_code=400, detail="Durasi audio tidak terbaca")

    aspect = req.aspectRatio or "9:16"
    scale = _target_scale(aspect)
    face_scale = _face_scale(aspect)
    kind = req.mediaKind or "video"
    want_lipsync = (req.mode or "lipsync") == "lipsync"
    job = "rmk_" + uuid.uuid4().hex[:12]
    tmp = _job_dir(job)
    os.makedirs(tmp, exist_ok=True)
    _set_job(job, status="processing", lipsyncApplied=False, reason="", downloadUrl="")

    t = threading.Thread(
        target=_process_remake,
        args=(job, media, audio, kind, want_lipsync, scale, face_scale, adur),
        daemon=True,
    )
    t.start()

    return {
        "job": job,
        "status": "processing",
        "statusUrl": _status_url(job),
        "downloadUrl": "",
        "lipsyncApplied": False,
        "reason": "",
    }


@router.get("/lipsync-result/{job}")
def lipsync_result(job: str, authorization: Optional[str] = Header(default=None)):
    """Polling status job remake."""
    _check_auth(authorization)
    st = _get_job(job)
    if st is None:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan: " + str(job))
    out = {"job": job}
    out.update(st)
    return out
