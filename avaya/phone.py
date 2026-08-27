# -*- coding: utf-8 -*-
"""avaya/phone.py — penarikan TELEPON (Phone).

Terpisah dari alur Chat agar avaya/client.py yang sudah jalan TIDAK tersentuh.
`AvayaPhoneClient` mewarisi `AvayaClient` dan HANYA mengubah satu hal pada
pencarian: `InteractionType` Chat (Id 10) -> Phone (Id 1). Selebihnya identik
dengan pencarian Chat (DateRange Between, FTSLanguage "en", dsb).

`probe_search()` menjalankan satu pencarian lalu mengembalikan header + sampel
baris MENTAH untuk inspeksi kolom (Increment 1).

`probe_media()` (Increment 2a/2b): ambil LOCATOR audio via GetMedia untuk satu
interaksi telepon, COBA AMBIL manifest .mpd dari recsvr01 memakai token VWT, lalu
(Increment 2b) UNDUH segmen audio (init + fragmen) + gabung jadi satu .mp4
sementara + transkode ke wav bila ffmpeg tersedia. KONTRAK GetMedia yang sudah
TERKONFIRMASI (data 24 Agu 2026):
  - startTime WAJIB GMT (kolom audio_start_time_gmt), BUKAN waktu lokal.
  - cli WAJIB diisi = personal_id baris tsb (agent ultra-ID). Tanpa cli -> HTTP 400.
Hasil sukses: mediaInfo[Audio].LocatorStatus=0, HttpPath=.mpd, VWT terisi,
EncryptionStatus=2, dan manifest DASH TANPA ContentProtection (tidak ada DRM;
segmen bisa langsung diunduh + didekode).

Catatan penyimpanan: probe_search & locator bersifat read-only. Increment 2b pada
probe_media MENYIMPAN berkas audio gabungan SEMENTARA di folder temp OS (untuk
verifikasi), BUKAN ke DB, dan tidak menyimpan kredensial.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET

import requests
import avaya.client as avc


class AvayaPhoneClient(avc.AvayaClient):
    """Klien pencarian Telepon: sama persis dengan Chat, hanya tipe interaksi
    diganti ke Phone (Id 1)."""

    def build_search_body(self, frm, to, sid, interaction_type=None):
        # Salin body Chat apa adanya lalu tukar HANYA Values InteractionType.
        itype = str(interaction_type or getattr(self, "_probe_itype", None) or "1")
        itype_text = {"1": "Phone", "10": "Chat"}.get(itype, "Phone")
        body = super().build_search_body(frm, to, sid)
        for sec in body.get("Sections", []) or []:
            if sec.get("Id") != "Interactions":
                continue
            for cat in sec.get("Categories", []) or []:
                for elt in cat.get("Elements", []) or []:
                    if elt.get("Id") == "InteractionType":
                        elt.setdefault("Params", {})["Values"] = [
                            {"Id": itype, "Text": itype_text}]
        return body

    def probe_search(self, day_from, day_to, interaction_type="1", limit_rows=25):
        """Uji satu pencarian (default Phone=Id 1); kembalikan header + sampel
        baris mentah. TIDAK menyimpan apa pun, TIDAK mengambil audio.

        Return dict: {search_id, interaction_type, count, maxExceeded, n_rows,
        columns, sample}. `sample` = list dict {nama_kolom: nilai_teks}.
        """
        if not self._logged_in:
            raise avc.AvayaAuthError("Belum login.")
        self._probe_itype = str(interaction_type or "1")
        frm = str(day_from)[:10] + "T00:00:00"
        to = str(day_to)[:10] + "T23:59:59"
        sid = self.create_search(frm, to)  # -> build_search_body (override) -> Phone
        self.exec_search(sid)
        info = self.get_header(sid)
        rows = self.get_data(sid)
        header = info.get("header") or []
        cols = [h.get("DataIndex") for h in header
                if isinstance(h, dict) and h.get("DataIndex")] or list(avc.DEFAULT_COLS)
        cm = self.col_map(header)
        try:
            n = max(1, int(limit_rows or 25))
        except Exception:
            n = 25
        sample = []
        for row in rows[:n]:
            d = {}
            for c in cols:
                idx = cm.get(c)
                val = ""
                if idx is not None and idx < len(row) and isinstance(row[idx], dict):
                    cell = row[idx]
                    val = cell.get("Text") or cell.get("Date") or cell.get("ItemId") or ""
                d[c] = val
            sample.append(d)
        return {
            "search_id": sid,
            "interaction_type": self._probe_itype,
            "count": info.get("count"),
            "maxExceeded": info.get("maxExceeded"),
            "n_rows": len(rows),
            "columns": cols,
            "sample": sample,
        }

    # ------------------------------------------------------------------
    # Increment 2a/2b — LOCATOR audio (GetMedia) + manifest .mpd + unduh segmen.
    # 2a/manifest: read-only. 2b (probe_media): unduh + simpan .mp4 SEMENTARA.
    # ------------------------------------------------------------------
    def get_media(self, sid, site_id, audio_channel, audio_module, start_time,
                  cli="", is_screen=False, numeric_ids=True):
        """Panggil GetMedia. Kembalikan dict {http_status, json, text, sent}.
        `text` = body respons mentah (dipotong) agar error WCF kelihatan.
        """
        def _v(x):
            s = str(x if x is not None else "").strip()
            if numeric_ids and s.isdigit():
                try:
                    return int(s)
                except Exception:
                    return s
            return s
        body = {
            "sid": _v(sid),
            "siteId": _v(site_id),
            "audioChannel": _v(audio_channel),
            "audioModule": _v(audio_module),
            "startTime": str(start_time or ""),
            "cli": str(cli or ""),
            "isScreen": bool(is_screen),
            "isVideo": False,
            "isShare": False,
            "isStreaming": True,
            "playbackSiteId": None,
            "isTPS": False,
        }
        url = "/Player/Services/PlayerService.svc/GetMedia"
        r = self._post(url, headers=self._headers_post(), data=json.dumps(body))
        txt = ""
        try:
            txt = r.text or ""
        except Exception:
            txt = ""
        return {
            "http_status": getattr(r, "status_code", None),
            "json": avc._safe_json(r),
            "text": txt[:2000],
            "sent": body,
        }

    @staticmethod
    def _vwt_kid(vwt):
        for part in str(vwt or "").replace(" ", ",").split(","):
            part = part.strip()
            if part.startswith("kid="):
                return part[4:]
        return ""

    def fetch_manifest(self, http_path, vwt, timeout=15):
        """Coba GET manifest .mpd dari recsvr01 memakai token VWT. Mencoba
        beberapa cara auth dan BERHENTI begitu dapat manifest DASH valid.
        Mengembalikan list hasil (status + potongan body). TIDAK menyimpan apa pun.
        """
        if not http_path:
            return []
        sess = getattr(self, "session", None)
        verify = getattr(self, "verify", False)
        plans = [
            {"label": "1: VWT saja (Authorization, tanpa cookie sesi)",
             "use_session": False, "headers": {"Authorization": vwt}},
            {"label": "2: tanpa auth (kontrol)",
             "use_session": False, "headers": {}},
            {"label": "3: sesi login + Authorization=VWT",
             "use_session": True, "headers": {"Authorization": vwt}},
        ]
        out = []
        for p in plans:
            rec = {"label": p["label"]}
            try:
                if p["use_session"] and sess is not None:
                    r = sess.get(http_path, headers=p["headers"],
                                 verify=verify, timeout=timeout)
                else:
                    r = requests.get(http_path, headers=p["headers"],
                                     verify=verify, timeout=timeout)
                body = ""
                try:
                    body = r.text or ""
                except Exception:
                    body = ""
                ct = None
                try:
                    ct = r.headers.get("Content-Type")
                except Exception:
                    ct = None
                rec.update({
                    "http_status": getattr(r, "status_code", None),
                    "content_type": ct,
                    "length": len(body),
                    "looks_like_dash": ("<MPD" in body or "urn:mpeg:dash" in body),
                    "has_content_protection": ("ContentProtection" in body),
                    "body_head": body[:1500],
                })
            except Exception as e:
                rec.update({"http_status": None, "error": "%r" % e})
            out.append(rec)
            if rec.get("http_status") == 200 and rec.get("looks_like_dash"):
                break
        return out

    @staticmethod
    def _seg_base(http_path):
        """Base URL untuk segmen = direktori manifest (buang 'manifest.mpd')."""
        if not http_path:
            return ""
        return http_path.rsplit("/", 1)[0] + "/"

    def _parse_mpd(self, mpd_text, base_url):
        """Parse manifest DASH: RepresentationID, template segmen, jumlah segmen
        (dari SegmentTimeline), plus info audio (codec/rate/channel/durasi).
        Bangun URL absolut init + tiap fragmen relatif terhadap base_url."""
        res = {"rep_id": "", "seg_count": 0, "init_url": "", "frag_urls": [],
               "duration": "", "codecs": "", "mime": "", "sample_rate": "",
               "bandwidth": "", "channels": ""}
        try:
            txt = (mpd_text or "").lstrip("\ufeff").strip()
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
                tl = st.find(