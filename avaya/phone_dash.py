# -*- coding: utf-8 -*-
"""avaya/phone_dash.py - util DASH audio Telepon (Increment 2b).

Dipisah dari phone.py agar tiap berkas kecil & aman di-push:
- seg_base(): direktori manifest (base URL segmen).
- parse_mpd(): RepresentationID, template segmen, jumlah segmen, info audio.
- download_and_save(): unduh init+fragmen via token VWT, gabung jadi .mp4
  SEMENTARA di temp OS, lalu (bila ada ffmpeg) transkode ke wav 16k mono.
Tidak menyimpan ke DB, tidak menyimpan kredensial.
"""
import os
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET

import requests


def seg_base(http_path):
    return (http_path.rsplit("/", 1)[0] + "/") if http_path else ""


def parse_mpd(mpd_text, base_url):
    res = {"rep_id": "", "seg_count": 0, "init_url": "", "frag_urls": [],
           "duration": "", "codecs": "", "mime": "", "sample_rate": "",
           "bandwidth": "", "channels": ""}
    try:
        txt = (mpd_text or "").strip()
        lt = txt.find("<")
        if lt > 0:
            txt = txt[lt:]
        ns = {"m": "urn:mpeg:dash:schema:mpd:2011"}
        root = ET.fromstring(txt)
        res["duration"] = root.get("mediaPresentationDuration") or ""
        rep = root.find(".//m:Representation", ns)
        if rep is not None:
            res["rep_id"] = rep.get("id") or ""
            res["codecs"] = rep.get("codecs") or ""
            res["mime"] = rep.get("mimeType") or ""
            res["sample_rate"] = rep.get("audioSamplingRate") or ""
            res["bandwidth"] = rep.get("bandwidth") or ""
        acc = root.find(".//m:AudioChannelConfiguration", ns)
        if acc is not None:
            res["channels"] = acc.get("value") or ""
        st = root.find(".//m:SegmentTemplate", ns)
        if st is not None:
            media_tmpl = st.get("media") or ""
            init_tmpl = st.get("initialization") or ""
            try:
                start_number = int(st.get("startNumber", "1"))
            except Exception:
                start_number = 1
            seg_count = 0
            tl = st.find("m:SegmentTimeline", ns)
            if tl is not None:
                for s in tl.findall("m:S", ns):
                    try:
                        rr = int(s.get("r", "0"))
                    except Exception:
                        rr = 0
                    seg_count += 1 + max(0, rr)
            res["seg_count"] = seg_count
            rid = res["rep_id"]

            def _sub(tmpl, number=None):
                o = tmpl.replace("$RepresentationID$", rid)
                if number is not None:
                    o = o.replace("$Number$", str(number))
                return o

            if init_tmpl:
                res["init_url"] = base_url + _sub(init_tmpl)
            if media_tmpl and seg_count:
                res["frag_urls"] = [base_url + _sub(media_tmpl, n)
                                    for n in range(start_number, start_number + seg_count)]
    except Exception as e:
        res["parse_error"] = "%r" % e
    return res


def download_and_save(client, http_path, vwt, timeout=30):
    verify = getattr(client, "verify", False)
    out = {"saved_path": "", "total_bytes": 0, "segments": 0, "segments_ok": 0,
           "init_ok": False, "init_bytes": 0, "seg_status": [], "manifest": {}, "decode": {}}
    try:
        rm = requests.get(http_path, headers={"Authorization": vwt}, verify=verify, timeout=20)
        mpd = rm.text or ""
        out["manifest"] = {"http_status": getattr(rm, "status_code", None), "length": len(mpd)}
    except Exception as e:
        out["manifest"] = {"error": "%r" % e}
        return out
    parsed = parse_mpd(mpd, seg_base(http_path))
    for k in ("duration", "rep_id", "seg_count", "codecs", "mime", "sample_rate", "bandwidth", "channels"):
        out["manifest"][k] = parsed.get(k)
    if parsed.get("parse_error"):
        out["manifest"]["parse_error"] = parsed["parse_error"]

    def _fetch(u):
        try:
            rr = requests.get(u, headers={"Authorization": vwt}, verify=verify, timeout=timeout)
            return getattr(rr, "status_code", None), (rr.content or b"")
        except Exception as e:
            return None, ("%r" % e).encode("utf-8", "replace")

    blobs = []
    iu = parsed.get("init_url") or ""
    if iu:
        code, data = _fetch(iu)
        ok = (code == 200)
        out["init_ok"] = ok
        out["init_bytes"] = len(data) if ok else 0
        if ok:
            blobs.append(data)
        out["seg_status"].append({"seg": "init", "http": code, "bytes": out["init_bytes"]})
    frags = parsed.get("frag_urls") or []
    out["segments"] = len(frags)
    ok_n = 0
    for i, u in enumerate(frags, 1):
        code, data = _fetch(u)
        if code == 200:
            blobs.append(data)
            ok_n += 1
        if i <= 10:
            out["seg_status"].append({"seg": i, "http": code, "bytes": (len(data) if code == 200 else 0)})
    out["segments_ok"] = ok_n
    out["total_bytes"] = sum(len(b) for b in blobs)
    try:
        safe = "".join(c for c in str(http_path) if c.isalnum())[-24:] or "audio"
        path = os.path.join(tempfile.gettempdir(), "awe_audio_%s.mp4" % safe)
        with open(path, "wb") as f:
            for b in blobs:
                f.write(b)
        out["saved_path"] = path
    except Exception as e:
        out["save_error"] = "%r" % e
    ff = shutil.which("ffmpeg")
    dec = {"ffmpeg_present": bool(ff)}
    if ff and out.get("saved_path"):
        wav = os.path.splitext(out["saved_path"])[0] + "_16k_mono.wav"
        try:
            p = subprocess.run([ff, "-y", "-i", out["saved_path"], "-ac", "1", "-ar", "16000", wav],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
            dec["ffmpeg_rc"] = p.returncode
            good = (p.returncode == 0 and os.path.exists(wav))
            dec["wav_path"] = wav if good else ""
            dec["wav_bytes"] = os.path.getsize(wav) if good else 0
            if p.stderr:
                dec["ffmpeg_log_tail"] = p.stderr.decode("utf-8", "replace")[-500:]
        except Exception as e:
            dec["ffmpeg_error"] = "%r" % e
    out["decode"] = dec
    return out
