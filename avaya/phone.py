# -*- coding: utf-8 -*-
"""avaya/phone.py — penarikan TELEPON (Phone).

Terpisah dari alur Chat agar avaya/client.py yang sudah jalan TIDAK tersentuh.
`AvayaPhoneClient` mewarisi `AvayaClient` dan HANYA mengubah satu hal pada
pencarian: `InteractionType` Chat (Id 10) -> Phone (Id 1). Selebihnya identik
dengan pencarian Chat (DateRange Between, FTSLanguage "en", dsb).

`probe_search()` menjalankan satu pencarian lalu mengembalikan header + sampel
baris MENTAH untuk inspeksi kolom (Increment 1).

`probe_media()` (Increment 2a) mengambil LOCATOR audio via GetMedia untuk satu
interaksi telepon. Karena format param GetMedia belum pasti (terutama startTime
GMT vs lokal, dan apakah `cli` wajib), probe mencoba beberapa kombinasi dan
mengembalikan status + teks error MENTAH tiap percobaan. TIDAK mengunduh audio
dan TIDAK menyimpan apa pun.
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
                  cli="", is_screen=False, numeric_ids=True):
        """Panggil GetMedia untuk satu interaksi telepon.

        Kembalikan dict {http_status, json, text, sent}. `text` = body respons
        mentah (dipotong) supaya error 400 WCF kelihatan. Hanya meminta lokasi
        media (URL .mpd + token) — TIDAK mengunduh byte audio.
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

    def probe_media(self, day_from, day_to):
        """Uji ambil LOCATOR audio: cari Phone pada rentang, ambil baris pertama
        yang punya audio_ch_num + audio_module_num, lalu coba GetMedia dengan
        matriks kombinasi (startTime GMT vs lokal, dengan/tanpa cli). Kembalikan
        field baris, kandidat cli, format waktu tersedia, dan hasil tiap
        percobaan (status + teks mentah). TIDAK mengunduh audio.
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

        def _full(row, key):
            idx = cm.get(key)
            if idx is None or idx >= len(row) or not isinstance(row[idx], dict):
                return {}
            return row[idx]

        def _txt(row, key):
            c = _full(row, key)
            return str(c.get("Text") or c.get("Date") or c.get("ItemId") or "").strip()

        def _date(row, key):
            c = _full(row, key)
            return str(c.get("Date") or c.get("Text") or "").strip()

        picked = None
        for row in rows:
            if _txt(row, "audio_ch_num") and _txt(row, "audio_module_num"):
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
            "sid": _txt(picked, "sid"),
            "site_id": _txt(picked, "site_id"),
            "audio_ch_num": _txt(picked, "audio_ch_num"),
            "audio_module_num": _txt(picked, "audio_module_num"),
            "ani": _txt(picked, "ani"),
            "dnis": _txt(picked, "dnis"),
            "interaction_type_id": _txt(picked, "interaction_type_id"),
        }
        candidates = {
            "personal_id": _txt(picked, "personal_id"),
            "call_id": _txt(picked, "call_id"),
            "uniquecallfield": _txt(picked, "uniquecallfield"),
            "string_extension": _txt(picked, "string_extension"),
            "sri": _txt(picked, "sri"),
            "transaction_id": _txt(picked, "transaction_id"),
            "media_type_bit_mask": _txt(picked, "media_type_bit_mask"),
        }
        time_fields = {
            "audio_start_time": _full(picked, "audio_start_time"),
            "audio_start_time_gmt": _full(picked, "audio_start_time_gmt"),
            "local_audio_start_time": _full(picked, "local_audio_start_time"),
        }
        gmt_iso = _date(picked, "audio_start_time_gmt")
        local_iso = _date(picked, "audio_start_time")
        personal_id = candidates["personal_id"]

        plans = [
            {"label": "A: GMT + tanpa cli", "start": gmt_iso, "cli": ""},
            {"label": "B: GMT + cli=personal_id", "start": gmt_iso, "cli": personal_id},
            {"label": "C: LOKAL + tanpa cli", "start": local_iso, "cli": ""},
            {"label": "D: LOKAL + cli=personal_id", "start": local_iso, "cli": personal_id},
        ]
        attempts = []
        for p in plans:
            try:
                r = self.get_media(
                    used["sid"], used["site_id"], used["audio_ch_num"],
                    used["audio_module_num"], p["start"], cli=p["cli"])
            except Exception as e:
                r = {"http_status": None, "json": None,
                     "text": "EXC: %r" % e, "sent": None}
            attempts.append({
                "label": p["label"],
                "http_status": r.get("http_status"),
                "sent": r.get("sent"),
                "text": r.get("text"),
                "json": r.get("json"),
            })

        media_summary = []
        for a in attempts:
            if a["json"] is not None:
                resp_preview = json.dumps(a["json"])[:160]
            else:
                resp_preview = (a["text"] or "")[:160]
            sent = a["sent"] or {}
            media_summary.append({
                "attempt": a["label"],
                "http": a["http_status"],
                "startTime": sent.get("startTime"),
                "cli": sent.get("cli"),
                "resp": resp_preview,
            })
        status_summary = " / ".join(
            "%s:%s" % (a["label"].split(":")[0], a["http_status"]) for a in attempts)

        return {
            "found_row": True,
            "search_id": sid,
            "n_rows": len(rows),
            "used": used,
            "http_status": status_summary,
            "media_summary": media_summary,
            "media_raw": {
                "candidates": candidates,
                "time_fields": time_fields,
                "attempts": attempts,
            },
        }
