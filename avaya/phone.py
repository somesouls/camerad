# -*- coding: utf-8 -*-
"""avaya/phone.py - penarikan TELEPON (Phone).

Terpisah dari alur Chat; avaya/client.py TIDAK tersentuh. AvayaPhoneClient =
PhoneMediaMixin + AvayaClient, hanya mengubah InteractionType Chat(10)->Phone(1).
- probe_search(): Increment 1 (header + sampel baris, read-only).
- probe_media(): Increment 2a/2b - locator GetMedia (startTime GMT + cli=personal_id)
  -> ambil manifest .mpd via VWT -> unduh+gabung segmen audio jadi .mp4 SEMENTARA
  (+ wav bila ada ffmpeg). Menyimpan berkas SEMENTARA di temp OS, BUKAN ke DB.
Logika berat ada di avaya/phone_media.py (locator) & avaya/phone_dash.py (unduh).
"""
import avaya.client as avc
import avaya.phone_media as pmedia
import avaya.phone_dash as pdash


class AvayaPhoneClient(pmedia.PhoneMediaMixin, avc.AvayaClient):
    """Klien pencarian Telepon: sama seperti Chat, tipe interaksi -> Phone (Id 1)."""

    def build_search_body(self, frm, to, sid, interaction_type=None):
        itype = str(interaction_type or getattr(self, "_probe_itype", None) or "1")
        itype_text = {"1": "Phone", "10": "Chat"}.get(itype, "Phone")
        body = super().build_search_body(frm, to, sid)
        for sec in body.get("Sections", []) or []:
            if sec.get("Id") != "Interactions":
                continue
            for cat in sec.get("Categories", []) or []:
                for elt in cat.get("Elements", []) or []:
                    if elt.get("Id") == "InteractionType":
                        elt.setdefault("Params", {})["Values"] = [{"Id": itype, "Text": itype_text}]
        return body

    def probe_search(self, day_from, day_to, interaction_type="1", limit_rows=25):
        if not self._logged_in:
            raise avc.AvayaAuthError("Belum login.")
        self._probe_itype = str(interaction_type or "1")
        sid = self.create_search(str(day_from)[:10] + "T00:00:00", str(day_to)[:10] + "T23:59:59")
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
        return {"search_id": sid, "interaction_type": self._probe_itype, "count": info.get("count"),
                "maxExceeded": info.get("maxExceeded"), "n_rows": len(rows), "columns": cols,
                "sample": sample}

    def probe_media(self, day_from, day_to):
        if not self._logged_in:
            raise avc.AvayaAuthError("Belum login.")
        self._probe_itype = "1"
        sid = self.create_search(str(day_from)[:10] + "T00:00:00", str(day_to)[:10] + "T23:59:59")
        self.exec_search(sid)
        info = self.get_header(sid)
        rows = self.get_data(sid)
        cm = self.col_map(info.get("header") or [])

        def _cell(row, key):
            i = cm.get(key)
            return row[i] if (i is not None and i < len(row) and isinstance(row[i], dict)) else {}

        def _txt(row, key):
            c = _cell(row, key)
            return str(c.get("Text") or c.get("Date") or c.get("ItemId") or "").strip()

        def _dat(row, key):
            c = _cell(row, key)
            return str(c.get("Date") or c.get("Text") or "").strip()

        picked = None
        for row in rows:
            if _txt(row, "audio_ch_num") and _txt(row, "audio_module_num"):
                picked = row
                break
        if picked is None:
            return {"found_row": False, "search_id": sid, "n_rows": len(rows),
                    "note": "Tidak ada baris audio (audio_ch_num+audio_module_num) pada rentang ini."}

        used = {k: _txt(picked, k) for k in ("sid", "site_id", "audio_ch_num",
                "audio_module_num", "ani", "dnis", "interaction_type_id")}
        personal_id = _txt(picked, "personal_id")
        gmt = _dat(picked, "audio_start_time_gmt")
        media = self.get_media(used["sid"], used["site_id"], used["audio_ch_num"],
                               used["audio_module_num"], gmt, cli=personal_id)
        mj = media.get("json") if isinstance(media, dict) else None
        items = mj.get("mediaInfo") if isinstance(mj, dict) else None
        audio = {}
        if isinstance(items, list):
            audio = next((it for it in items if isinstance(it, dict) and it.get("MediaType") == "Audio"), {}) or {}
        http_path = audio.get("HttpPath") or ""
        vwt = audio.get("VWT") or ""
        enc = audio.get("EncryptionStatus")
        locstat = audio.get("LocatorStatus")

        attempts = self.fetch_manifest(http_path, vwt) if http_path else []
        got_dash = any(a.get("http_status") == 200 and a.get("looks_like_dash") for a in attempts)
        download = None
        if http_path and vwt and got_dash:
            try:
                download = pdash.download_and_save(self, http_path, vwt)
            except Exception as e:
                download = {"error": "%r" % e}

        summary = [{"item": "GetMedia (locator)",
                    "http": media.get("http_status") if isinstance(media, dict) else None,
                    "locator_status": locstat, "encryption": enc,
                    "detail": "Audio .mpd + VWT terisi" if http_path else "locator gagal"}]
        for a in attempts:
            det = ("ERR: " + str(a.get("error"))[:80]) if a.get("error") else (
                "%s • len=%s%s%s" % (a.get("content_type") or "?", a.get("length"),
                                     " • DASH" if a.get("looks_like_dash") else "",
                                     " • ContentProtection" if a.get("has_content_protection") else ""))
            summary.append({"item": "Manifest " + str(a.get("label", "")), "http": a.get("http_status"),
                            "locator_status": "", "encryption": "", "detail": det})
        status = "Locator http=%s locStatus=%s enc=%s | Manifest %s" % (
            media.get("http_status") if isinstance(media, dict) else None, locstat, enc,
            " / ".join("%s:%s" % (str(a.get("label", "?")).split(":")[0], a.get("http_status")) for a in attempts) or "-")

        if isinstance(download, dict) and not download.get("error"):
            m2 = download.get("manifest") or {}
            d2 = download.get("decode") or {}
            summary.append({"item": "Audio (DASH)", "http": m2.get("http_status"), "locator_status": "",
                            "encryption": "", "detail": "%s • %s Hz • ch=%s • dur=%s • %s seg" % (
                                m2.get("codecs") or "?", m2.get("sample_rate") or "?", m2.get("channels") or "?",
                                m2.get("duration") or "?", m2.get("seg_count") or "?")})
            summary.append({"item": "Unduh + gabung .mp4", "http": "", "locator_status": "", "encryption": "",
                            "detail": "init %s • fragmen %s/%s • total %s B" % (
                                "ok" if download.get("init_ok") else "GAGAL", download.get("segments_ok"),
                                download.get("segments"), download.get("total_bytes"))})
            summary.append({"item": "ffmpeg -> wav", "http": "", "locator_status": "", "encryption": "",
                            "detail": ("wav %s B" % d2.get("wav_bytes")) if d2.get("wav_path") else (
                                "ada (transkode gagal)" if d2.get("ffmpeg_present") else "tidak ada (2c: PyAV/faster-whisper)")})
            status += " | Unduh %s/%s seg, %s B%s" % (
                download.get("segments_ok"), download.get("segments"), download.get("total_bytes"),
                (", wav %s B" % d2.get("wav_bytes")) if d2.get("wav_path") else "")
        elif isinstance(download, dict):
            summary.append({"item": "Unduh audio", "http": "", "locator_status": "", "encryption": "",
                            "detail": "ERROR: " + str(download.get("error"))[:80]})
            status += " | Unduh ERROR"

        return {"found_row": True, "search_id": sid, "n_rows": len(rows), "used": used,
                "http_status": status, "media_summary": summary,
                "media_raw": {"locator_audio": {"http_path": http_path, "encryption_status": enc,
                                                "locator_status": locstat, "file_name": audio.get("FileName") or "",
                                                "media_start_time": audio.get("StartTime") or "",
                                                "vwt_present": bool(vwt), "vwt_kid": self._vwt_kid(vwt),
                                                "vwt_preview": (vwt[:48] + "...") if vwt else ""},
                              "manifest_attempts": attempts, "download": download,
                              "used_extra": {"personal_id": personal_id, "gmt_start": gmt}}}
