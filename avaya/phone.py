# -*- coding: utf-8 -*-
"""avaya/phone.py — penarikan TELEPON (Phone).

Terpisah dari alur Chat agar avaya/client.py yang sudah jalan TIDAK tersentuh.
`AvayaPhoneClient` mewarisi `AvayaClient` dan HANYA mengubah satu hal pada
pencarian: `InteractionType` Chat (Id 10) -> Phone (Id 1). Selebihnya identik
dengan pencarian Chat (DateRange Between, FTSLanguage "en", dsb).

`probe_search()` menjalankan satu pencarian lalu mengembalikan header + sampel
baris MENTAH untuk inspeksi kolom (Increment 1).

`probe_media()` (Increment 2a/2b-probe): ambil LOCATOR audio via GetMedia untuk
satu interaksi telepon, lalu COBA AMBIL manifest .mpd dari recsvr01 memakai token
VWT. KONTRAK GetMedia yang sudah TERKONFIRMASI (data 24 Agu 2026):
  - startTime WAJIB GMT (kolom audio_start_time_gmt), BUKAN waktu lokal.
  - cli WAJIB diisi = personal_id baris tsb (agent ultra-ID). Tanpa cli -> HTTP 400.
Hasil sukses: mediaInfo[Audio].LocatorStatus=0, HttpPath=.mpd, VWT terisi,
EncryptionStatus=2.

Semua fungsi di sini READ-ONLY: TIDAK mengunduh byte audio ke disk dan TIDAK
menyimpan apa pun.
"""
import json
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
    # Increment 2a/2b — LOCATOR audio (GetMedia) + ambil manifest .mpd.
    # Read-only: tidak mengunduh byte audio, tidak menyimpan apa pun.
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

    def probe_media(self, day_from, day_to):
        """Uji rantai audio (read-only): cari Phone -> ambil baris pertama
        ber-audio -> GetMedia dgn kontrak terkonfirmasi (startTime GMT +
        cli=personal_id) -> coba ambil manifest .mpd via VWT. Laporkan locator +
        hasil fetch manifest. TIDAK mengunduh audio & TIDAK menyimpan apa pun.
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
        personal_id = _txt(picked, "personal_id")
        gmt_iso = _date(picked, "audio_start_time_gmt")

        media = self.get_media(
            used["sid"], used["site_id"], used["audio_ch_num"],
            used["audio_module_num"], gmt_iso, cli=personal_id)
        mj = media.get("json") if isinstance(media, dict) else None

        all_items = []
        audio_item = None
        if isinstance(mj, dict):
            mi = mj.get("mediaInfo")
            if isinstance(mi, list):
                all_items = mi
                for it in mi:
                    if isinstance(it, dict) and it.get("MediaType") == "Audio":
                        audio_item = it
                        break

        http_path = ""
        vwt = ""
        enc = None
        locstat = None
        fname = ""
        mstart = ""
        if isinstance(audio_item, dict):
            http_path = audio_item.get("HttpPath") or ""
            vwt = audio_item.get("VWT") or ""
            enc = audio_item.get("EncryptionStatus")
            locstat = audio_item.get("LocatorStatus")
            fname = audio_item.get("FileName") or ""
            mstart = audio_item.get("StartTime") or ""

        manifest_attempts = self.fetch_manifest(http_path, vwt) if http_path else []

        media_items = []
        for it in all_items:
            if not isinstance(it, dict):
                continue
            hp = it.get("HttpPath") or ""
            media_items.append({
                "MediaType": it.get("MediaType"),
                "LocatorStatus": it.get("LocatorStatus"),
                "EncryptionStatus": it.get("EncryptionStatus"),
                "FileName": it.get("FileName") or "",
                "HttpPath_head": (hp[:80] + "\u2026") if len(hp) > 80 else hp,
                "has_VWT": bool(it.get("VWT")),
            })

        media_summary = [{
            "item": "GetMedia (locator)",
            "http": media.get("http_status") if isinstance(media, dict) else None,
            "locator_status": locstat,
            "encryption": enc,
            "detail": ("Audio .mpd + VWT terisi" if http_path else "TIDAK ada HttpPath audio (locator gagal)"),
        }]
        for a in manifest_attempts:
            if a.get("error"):
                det = "ERR: " + str(a.get("error"))[:90]
            else:
                det = "%s • len=%s%s%s" % (
                    a.get("content_type") or "?",
                    a.get("length"),
                    " • DASH" if a.get("looks_like_dash") else "",
                    " • ContentProtection" if a.get("has_content_protection") else "")
            media_summary.append({
                "item": "Manifest " + str(a.get("label", "")),
                "http": a.get("http_status"),
                "locator_status": "",
                "encryption": "",
                "detail": det,
            })

        man_str = " / ".join(
            "%s:%s" % (str(a.get("label", "?")).split(":")[0], a.get("http_status"))
            for a in manifest_attempts) or "(dilewati: tak ada locator)"
        status_summary = "Locator http=%s locStatus=%s enc=%s | Manifest %s" % (
            (media.get("http_status") if isinstance(media, dict) else None),
            locstat, enc, man_str)

        return {
            "found_row": True,
            "search_id": sid,
            "n_rows": len(rows),
            "used": used,
            "http_status": status_summary,
            "media_summary": media_summary,
            "media_raw": {
                "locator_audio": {
                    "http_path": http_path,
                    "encryption_status": enc,
                    "locator_status": locstat,
                    "file_name": fname,
                    "media_start_time": mstart,
                    "vwt_present": bool(vwt),
                    "vwt_kid": self._vwt_kid(vwt),
                    "vwt_preview": (vwt[:48] + "\u2026") if vwt else "",
                },
                "media_items": media_items,
                "manifest_attempts": manifest_attempts,
                "used_extra": {"personal_id": personal_id, "gmt_start": gmt_iso},
            },
        }
