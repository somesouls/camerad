"""
OtoPost Clip Server

Semua pekerjaan berat pindah ke server (bukan di HP):
- POST /transcript : ambil transkrip ASLI (subtitle manual/otomatis) via yt-dlp
- POST /clips      : download video HD -> potong per-segmen -> reframe 9:16 ke wajah (ffmpeg + OpenCV) -> opsional burn-in subtitle
- POST /upload     : upload media (video/foto/audio) untuk fitur Remake Suara/Lipsync (lihat remake.py)
- POST /lipsync    : tempel suara baru ke media dengan aturan durasi (lihat remake.py)
- GET  /           : health check
- GET  /files/...  : unduh hasil clip

Opsional pakai token (env CLIP_SERVER_TOKEN) & cookies (env YTDLP_COOKIES) bila IP server diblok YouTube.
"""
import os
import re
import glob
import uuid
import subprocess
from typing import List, Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import yt_dlp

from remake import router as remake_router

WORK_DIR = os.environ.get("CLIP_WORK_DIR", "/data/clips")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
CLIP_SERVER_TOKEN = os.environ.get("CLIP_SERVER_TOKEN", "")
COOKIES_FILE = os.environ.get("YTDLP_COOKIES", "")
os.makedirs(WORK_DIR, exist_ok=True)

app = FastAPI(title="OtoPost Clip Server", version="1.3")
app.mount("/files", StaticFiles(directory=WORK_DIR), name="files")
app.include_router(remake_router)


def _check_auth(authorization: Optional[str]):
    if not CLIP_SERVER_TOKEN:
        return
    if authorization != ("Bearer " + CLIP_SERVER_TOKEN):
        raise HTTPException(status_code=401, detail="Token tidak valid")


def _file_url(rel_path: str) -> str:
    return (PUBLIC_BASE_URL if PUBLIC_BASE_URL else "") + "/files/" + rel_path


def _base_opts() -> dict:
    o = {"quiet": True, "no_warnings": True}
    if COOKIES_FILE and os.path.exists(COOKIES_FILE):
        o["cookiefile"] = COOKIES_FILE
    return o


# ---------------- Transcript ----------------
class TranscriptRequest(BaseModel):
    url: str
    langs: Optional[List[str]] = None


def _parse_vtt(path: str):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    blocks = re.split(r"\n\n+", content)
    ts_re = re.compile(r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})\s*-->")
    segs = []
    for b in blocks:
        m = ts_re.search(b)
        if not m:
            continue
        h, mnt, s, ms = map(int, m.groups())
        start = h * 3600 + mnt * 60 + s + ms / 1000.0
        lines = [
            l for l in b.splitlines()
            if "-->" not in l and not l.strip().isdigit() and l.strip()
            and not l.strip().startswith("WEBVTT") and not l.strip().startswith("Kind:")
            and not l.strip().startswith("Language:")
        ]
        clean = re.sub(r"<[^>]+>", " ", " ".join(lines))
        clean = re.sub(r"\s+", " ", clean).strip()
        if clean:
            segs.append({"startSec": round(start, 2), "text": clean})
    # buang duplikat berurutan (umum di auto-subs)
    dedup, full, prev = [], [], None
    for seg in segs:
        if seg["text"] != prev:
            dedup.append(seg)
            full.append(seg["text"])
        prev = seg["text"]
    return dedup, " ".join(full)


@app.post("/transcript")
def transcript(req: TranscriptRequest, authorization: Optional[str] = Header(default=None)):
    _check_auth(authorization)

    requested_langs = req.langs or ["id", "id-ID", "en", "en-US"]

    job = "t_" + uuid.uuid4().hex[:12]
    tmp = os.path.join(WORK_DIR, job)
    os.makedirs(tmp, exist_ok=True)

    # Kelompokkan bahasa: prioritaskan Indonesia, lalu Inggris.
    lang_groups = [
        [lg for lg in requested_langs if lg.lower().startswith("id")],
        [lg for lg in requested_langs if lg.lower().startswith("en")],
    ]

    # Buang group kosong
    lang_groups = [g for g in lang_groups if g]

    info = None
    vtts = []

    # Ambil metadata video terlebih dahulu.
    try:
        meta_opts = _base_opts()
        meta_opts.update({
            "skip_download": True,
        })

        with yt_dlp.YoutubeDL(meta_opts) as ydl:
            info = ydl.extract_info(req.url, download=False)

    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail="Gagal mengambil info video: " + str(e)
        )

    # Coba subtitle satu kelompok bahasa pada satu waktu.
    for langs in lang_groups:
        try:
            opts = _base_opts()
            opts.update({
                "skip_download": True,
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitleslangs": langs,
                "subtitlesformat": "vtt",
                "outtmpl": os.path.join(tmp, "%(id)s.%(ext)s"),
            })

            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.extract_info(req.url, download=True)

            vtts = glob.glob(os.path.join(tmp, "*.vtt"))

            if vtts:
                break

        except Exception as e:
            # Subtitle gagal/429 bukan berarti video gagal.
            print(
                "WARNING: subtitle gagal untuk",
                langs,
                ":",
                str(e)
            )
            continue

    def rank(p):
        name = os.path.basename(p).lower()

        for i, lg in enumerate(requested_langs):
            if ("." + lg.lower() + ".") in name:
                return i

        return len(requested_langs) + 1

    vtts.sort(key=rank)

    if not vtts:
        return {
            "videoId": info.get("id"),
            "title": info.get("title"),
            "channelName": info.get("uploader"),
            "durationSec": info.get("duration"),
            "hasTranscript": False,
            "transcriptText": "",
            "segments": [],
        }

    segs, full = _parse_vtt(vtts[0])

    return {
        "videoId": info.get("id"),
        "title": info.get("title"),
        "channelName": info.get("uploader"),
        "durationSec": info.get("duration"),
        "hasTranscript": bool(full),
        "transcriptText": full,
        "segments": segs,
    }


# ---------------- Clips ----------------
class Segment(BaseModel):
    startSec: float
    endSec: float
    title: Optional[str] = None


class ClipsRequest(BaseModel):
    url: str
    segments: List[Segment]
    aspectRatio: Optional[str] = "9:16"
    reframe: Optional[bool] = True
    maxHeight: Optional[int] = 1080
    subtitle: Optional[bool] = False
    subtitleStyle: Optional[str] = "clean"


def _download_source(url: str, tmp: str, max_h: int) -> str:
    fmt = "bv*[height<=" + str(max_h) + "]+ba/b[height<=" + str(max_h) + "]/best"
    opts = _base_opts()
    opts.update({
        "format": fmt,
        "merge_output_format": "mp4",
        "outtmpl": os.path.join(tmp, "src.%(ext)s"),
    })
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.extract_info(url, download=True)
    cands = glob.glob(os.path.join(tmp, "src.*"))
    if not cands:
        raise HTTPException(status_code=502, detail="Video gagal diunduh")
    return cands[0]


def _probe_dim(path: str):
    try:
        out = subprocess.check_output([
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", path,
        ]).decode().strip()
        w, h = out.split("x")[:2]
        return int(w), int(h)
    except Exception:
        return None, None


def _face_center_x(path: str, t: float):
    try:
        import cv2
        cap = cv2.VideoCapture(path)
        cap.set(cv2.CAP_PROP_POS_MSEC, max(t, 0.0) * 1000)
        ok, frame = cap.read()
        cap.release()
        if not ok or frame is None:
            return None
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        faces = cascade.detectMultiScale(gray, 1.1, 5, minSize=(60, 60))
        if len(faces) == 0:
            return None
        fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])
        return fx + fw / 2.0
    except Exception:
        return None


def _build_vf(src_w, src_h, aspect, reframe, face_cx):
    if not src_w or not src_h:
        return "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
    if aspect == "9:16":
        crop_w = min(int(round(src_h * 9 / 16)), src_w)
        if reframe and face_cx is not None:
            x0 = int(round(face_cx - crop_w / 2))
        else:
            x0 = int(round((src_w - crop_w) / 2))
        x0 = max(0, min(x0, src_w - crop_w))
        return "crop=" + str(crop_w) + ":" + str(src_h) + ":" + str(x0) + ":0,scale=1080:1920"
    if aspect == "1:1":
        side = min(src_w, src_h)
        x0 = int(round((src_w - side) / 2))
        y0 = int(round((src_h - side) / 2))
        return "crop=" + str(side) + ":" + str(side) + ":" + str(x0) + ":" + str(y0) + ",scale=1080:1080"
    return "scale=-2:1080"


# ---------------- Subtitle helpers ----------------
def _srt_time(sec: float) -> str:
    if sec < 0:
        sec = 0.0
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    if ms >= 1000:
        ms = 999
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


def _parse_vtt_cues(path: str):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    blocks = re.split(r"\n\n+", content)
    ts_re = re.compile(
        r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[.,](\d{3})"
    )
    cues = []
    for b in blocks:
        m = ts_re.search(b)
        if not m:
            continue
        h1, m1, s1, ms1, h2, m2, s2, ms2 = map(int, m.groups())
        start = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000.0
        end = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000.0
        lines = [
            l for l in b.splitlines()
            if "-->" not in l and not l.strip().isdigit() and l.strip()
            and not l.strip().startswith("WEBVTT") and not l.strip().startswith("Kind:")
            and not l.strip().startswith("Language:")
        ]
        clean = re.sub(r"<[^>]+>", " ", " ".join(lines))
        clean = re.sub(r"\s+", " ", clean).strip()
        if clean:
            cues.append({"start": round(start, 3), "end": round(end, 3), "text": clean})
    # buang duplikat berurutan (umum di auto-subs)
    dedup, prev = [], None
    for c in cues:
        if c["text"] != prev:
            dedup.append(c)
        prev = c["text"]
    return dedup


def _download_vtt_cues(url: str, tmp: str):
    langs = ["id", "id-ID", "en", "en-US"]
    sub_dir = os.path.join(tmp, "subs")
    os.makedirs(sub_dir, exist_ok=True)
    opts = _base_opts()
    opts.update({
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": langs,
        "subtitlesformat": "vtt",
        "outtmpl": os.path.join(sub_dir, "%(id)s.%(ext)s"),
    })
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=True)
    except Exception as e:
        print("WARNING: subtitle download gagal:", str(e))
    vtts = glob.glob(os.path.join(sub_dir, "*.vtt"))

    def rank(p):
        name = os.path.basename(p).lower()
        for i, lg in enumerate(langs):
            if ("." + lg.lower() + ".") in name:
                return i
        return len(langs) + 1

    vtts.sort(key=rank)
    if not vtts:
        return []
    return _parse_vtt_cues(vtts[0])


def _write_window_srt(cues, win_start: float, win_end: float, srt_path: str) -> bool:
    idx = 1
    lines = []
    for c in cues:
        cs = float(c["start"])
        ce = float(c["end"])
        if ce <= win_start or cs >= win_end:
            continue
        rel_start = max(0.0, cs - win_start)
        rel_end = min(win_end, ce) - win_start
        if rel_end <= rel_start:
            rel_end = rel_start + 0.5
        lines.append(str(idx))
        lines.append(_srt_time(rel_start) + " --> " + _srt_time(rel_end))
        lines.append(c["text"])
        lines.append("")
        idx += 1
    if not lines:
        return False
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return True


def _sub_style(style: str) -> str:
    s = (style or "clean").lower()
    styles = {
        "clean": "FontName=Arial,Fontsize=18,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV=60",
        "bold": "FontName=Arial,Fontsize=22,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=1,Alignment=2,MarginV=60",
        "box": "FontName=Arial,Fontsize=18,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H90000000,BorderStyle=3,Outline=0,Shadow=0,Alignment=2,MarginV=60",
        "yellow": "FontName=Arial,Fontsize=20,Bold=1,PrimaryColour=&H0000FFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=1,Alignment=2,MarginV=60",
        "tiktok": "FontName=Arial,Fontsize=24,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=4,Shadow=2,Alignment=2,MarginV=90",
        "karaoke": "FontName=Arial,Fontsize=24,Bold=1,PrimaryColour=&H0000FFFF,SecondaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=1,Alignment=2,MarginV=80",
        "minimal": "FontName=Arial,Fontsize=16,PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,BorderStyle=1,Outline=1,Shadow=0,Alignment=2,MarginV=50",
        "highlight": "FontName=Arial,Fontsize=20,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&HB0000000,BorderStyle=3,Outline=0,Shadow=0,Alignment=2,MarginV=70",
        "neon": "FontName=Arial,Fontsize=22,Bold=1,PrimaryColour=&H00F0FF00,OutlineColour=&H00FF00AA,BorderStyle=1,Outline=3,Shadow=2,Alignment=2,MarginV=70",
        "pop": "FontName=Arial,Fontsize=26,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=5,Shadow=2,Alignment=2,MarginV=90",
    }
    return styles.get(s, styles["clean"])


@app.post("/clips")
def clips(req: ClipsRequest, authorization: Optional[str] = Header(default=None)):
    _check_auth(authorization)
    if not req.segments:
        raise HTTPException(status_code=400, detail="Tidak ada segmen")
    job = uuid.uuid4().hex[:12]
    tmp = os.path.join(WORK_DIR, job)
    os.makedirs(tmp, exist_ok=True)
    try:
        src = _download_source(req.url, tmp, req.maxHeight or 1080)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail="Gagal download: " + str(e))
    src_w, src_h = _probe_dim(src)

    # Ambil cue subtitle sekali saja bila subtitle diminta.
    cues = []
    if req.subtitle:
        try:
            cues = _download_vtt_cues(req.url, tmp)
        except Exception as e:
            print("WARNING: gagal ambil subtitle:", str(e))
            cues = []

    style_str = _sub_style(req.subtitleStyle or "clean")
    results = []
    for idx, seg in enumerate(req.segments):
        start = max(0.0, float(seg.startSec))
        end = float(seg.endSec)
        if end <= start:
            continue
        mid = start + (end - start) / 2.0
        face_cx = _face_center_x(src, mid) if req.reframe else None
        vf = _build_vf(src_w, src_h, req.aspectRatio or "9:16", bool(req.reframe), face_cx)
        subtitled = False
        srt_name = "clip_" + str(idx + 1) + ".srt"
        srt_path = os.path.join(tmp, srt_name)
        if req.subtitle and cues:
            try:
                wrote = _write_window_srt(cues, start, end, srt_path)
                if wrote:
                    vf = vf + ",subtitles=" + srt_name + ":force_style='" + style_str + "'"
                    subtitled = True
            except Exception as e:
                print("WARNING: gagal tulis srt:", str(e))
        out_name = "clip_" + str(idx + 1) + ".mp4"
        out_path = os.path.join(tmp, out_name)
        cmd = [
            "ffmpeg", "-y", "-ss", str(start), "-to", str(end), "-i", src,
            "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", out_name,
        ]
        proc = subprocess.run(cmd, capture_output=True, cwd=tmp)
        if proc.returncode != 0 or not os.path.exists(out_path):
            continue
        results.append({
            "index": idx + 1,
            "title": seg.title or ("Clip " + str(idx + 1)),
            "startSec": start, "endSec": end,
            "reframed": face_cx is not None,
            "subtitled": subtitled,
            "downloadUrl": _file_url(job + "/" + out_name),
        })
    if not results:
        raise HTTPException(status_code=500, detail="Semua segmen gagal dipotong")
    return {"job": job, "clips": results}


@app.get("/")
def health():
    return {"ok": True, "service": "otopost-clip-server"}
