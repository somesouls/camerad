# -*- coding: utf-8 -*-
"""avaya/phone_media.py - mixin locator audio Telepon (GetMedia + manifest).

Dipisah dari phone.py agar berkas kecil. PhoneMediaMixin dipakai oleh
AvayaPhoneClient. Kontrak GetMedia terkonfirmasi: startTime WAJIB GMT, cli WAJIB
= personal_id. Bagian ini read-only (tidak unduh byte audio, tidak simpan).
"""
import json

import requests
import avaya.client as avc


class PhoneMediaMixin(object):
    def get_media(self, sid, site_id, audio_channel, audio_module, start_time,
                  cli="", is_screen=False, numeric_ids=True):
        """Panggil GetMedia; kembalikan {http_status, json, text, sent}."""
        def _v(x):
            s = str(x if x is not None else "").strip()
            if numeric_ids and s.isdigit():
                try:
                    return int(s)
                except Exception:
                    return s
            return s
        body = {"sid": _v(sid), "siteId": _v(site_id), "audioChannel": _v(audio_channel),
                "audioModule": _v(audio_module), "startTime": str(start_time or ""),
                "cli": str(cli or ""), "isScreen": bool(is_screen), "isVideo": False,
                "isShare": False, "isStreaming": True, "playbackSiteId": None, "isTPS": False}
        r = self._post("/Player/Services/PlayerService.svc/GetMedia",
                       headers=self._headers_post(), data=json.dumps(body))
        try:
            txt = r.text or ""
        except Exception:
            txt = ""
        return {"http_status": getattr(r, "status_code", None), "json": avc._safe_json(r),
                "text": txt[:2000], "sent": body}

    @staticmethod
    def _vwt_kid(vwt):
        for part in str(vwt or "").replace(" ", ",").split(","):
            part = part.strip()
            if part.startswith("kid="):
                return part[4:]
        return ""

    def fetch_manifest(self, http_path, vwt, timeout=15):
        """Coba GET manifest .mpd via token VWT; berhenti begitu dapat DASH valid.
        Kembalikan list hasil (status + potongan body). Tidak menyimpan apa pun."""
        if not http_path:
            return []
        verify = getattr(self, "verify", False)
        sess = getattr(self, "session", None)
        plans = [("1: VWT saja (Authorization, tanpa cookie sesi)", False, {"Authorization": vwt}),
                 ("2: tanpa auth (kontrol)", False, {}),
                 ("3: sesi login + Authorization=VWT", True, {"Authorization": vwt})]
        out = []
        for label, use_sess, headers in plans:
            rec = {"label": label}
            try:
                getter = sess.get if (use_sess and sess is not None) else requests.get
                r = getter(http_path, headers=headers, verify=verify, timeout=timeout)
                try:
                    body = r.text or ""
                except Exception:
                    body = ""
                try:
                    ct = r.headers.get("Content-Type")
                except Exception:
                    ct = None
                rec.update({"http_status": getattr(r, "status_code", None), "content_type": ct,
                            "length": len(body),
                            "looks_like_dash": ("<MPD" in body or "urn:mpeg:dash" in body),
                            "has_content_protection": ("ContentProtection" in body),
                            "body_head": body[:1500]})
            except Exception as e:
                rec.update({"http_status": None, "error": "%r" % e})
            out.append(rec)
            if rec.get("http_status") == 200 and rec.get("looks_like_dash"):
                break
        return out
