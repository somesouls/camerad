# -*- coding: utf-8 -*-
"""avaya/phone.py — penarikan TELEPON (Phone).

Terpisah dari alur Chat agar avaya/client.py yang sudah jalan TIDAK tersentuh.
`AvayaPhoneClient` mewarisi `AvayaClient` dan HANYA mengubah satu hal pada
pencarian: `InteractionType` Chat (Id 10) -> Phone (Id 1). Selebihnya identik
dengan pencarian Chat (DateRange Between, FTSLanguage "en", dsb).

`probe_search()` menjalankan satu pencarian lalu mengembalikan header + sampel
baris MENTAH untuk inspeksi kolom (Increment 1).

`probe_media()` (Increment 2a) mengambil LOCATOR audio (URL .mpd + token) untuk
satu interaksi telepon via GetMedia — untuk memverifikasi audio bisa diakses
dari sisi server SEBELUM membangun unduh/decode/STT. TIDAK mengunduh audio dan
TIDAK menyimpan apa pun.
"""
import json
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
    # Increment 2a — LOCATOR audio (GetMedia). Read-only: tidak unduh/simpan.
    # ------------------------------------------------------------------
    def get_media(self, sid, site_id, audio_channel, audio_module, start_time,
                  cli="", is_screen=False):
        """Panggil GetMedia untuk satu interaksi telepon.

        Kembalikan (json_mentah, http_status, body_terkirim). Hanya meminta
        lokasi media (URL .mpd + token) — TIDAK mengunduh byte audio.
        """
        def _numish(v):
            s = str(v if v is not None else "").strip()
            return int(s) if s.isdigit() else s
        body = {
            "sid": _numish(sid),
            "siteId": _numish(site_id),
            "audioChannel": _numish(audio_channel),
            "audioModule": _numish(audio_module),
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
        return avc._safe_json(r), getattr(r, "status_code", None), body

    def probe_media(self, day_from, day_to):
        """Uji ambil LOCATOR audio: cari Phone pada rentang, ambil baris pertama
        yang punya audio_ch_num + audio_module_num, panggil GetMedia, lalu
        kembalikan field yang dipakai + respons mentah (untuk melihat
        HttpPath/.mpd, token, EncryptionStatus, LocatorStatus).

        TIDAK mengunduh audio & TIDAK menyimpan apa pun.
        """
        if not self._logged_in:
            raise avc.AvayaAuthError("Belum login.")
        self._probe_itype = "1"  # Phone
        frm = str(day_from)[:10] + "T00:00:00"
        to = str(day_to)[:10] + "T23:59:59"
        sid = self.create_search(frm, to)
        self.exec_search(sid)
        info = self.get_header(sid)
        rows = self.get_data(sid)
        header = info.get("header") or []
        cm = self.col_map(header)

        def _cell(row, key):
            idx = cm.get(key)
            if idx is None or idx >= len(row) or not isinstance(row[idx], dict):
                return ""
            c = row[idx]
            return c.get("Text") or c.get("Date") or c.get("ItemId") or ""

        picked = None
        for row in rows:
            ch = str(_cell(row, "audio_ch_num")).strip()
            mod = str(_cell(row, "audio_module_num")).strip()
            if ch and mod:
                picked = row
                break
        if picked is None:
            return {
                "found_row": False,
                "search_id": sid,
                "n_rows": len(rows),
                "note": "Tidak ada baris dengan audio_ch_num + audio_module_num pada rentang ini.",
            }

        used = {
            "sid": str(_cell(picked, "sid")).strip(),
            "site_id": str(_cell(picked, "site_id")).strip(),
            "audio_ch_num": str(_cell(picked, "audio_ch_num")).strip(),
            "audio_module_num": str(_cell(picked, "audio_module_num")).strip(),
            "audio_start_time": str(_cell(picked, "audio_start_time")).strip(),
            "ani": str(_cell(picked, "ani")).strip(),
            "dnis": str(_cell(picked, "dnis")).strip(),
            "interaction_type_id": str(_cell(picked, "interaction_type_id")).strip(),
        }
        media, status, sent = self.get_media(
            used["sid"], used["site_id"], used["audio_ch_num"],
            used["audio_module_num"], used["audio_start_time"])

        summary = []
        mi = None
        if isinstance(media, dict):
            mi = (media.get("mediaInfo") or media.get("MediaInfo")
                  or media.get("Media") or media.get("Locators"))
        if isinstance(mi, list):
            for item in mi:
                if not isinstance(item, dict):
                    continue
                entry = {}
                for k, v in item.items():
                    if isinstance(v, (str, int, float, bool)) or v is None:
                        entry[k] = v
                summary.append(entry)

        return {
            "found_row": True,
            "search_id": sid,
            "n_rows": len(rows),
            "used": used,
            "sent_body": sent,
            "http_status": status,
            "media_summary": summary,
            "media_raw": media,
        }
