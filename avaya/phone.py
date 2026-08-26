# -*- coding: utf-8 -*-
"""avaya/phone.py — Increment 1 penarikan TELEPON (Phone).

Terpisah dari alur Chat agar avaya/client.py yang sudah jalan TIDAK tersentuh.
`AvayaPhoneClient` mewarisi `AvayaClient` dan HANYA mengubah satu hal pada
pencarian: `InteractionType` Chat (Id 10) -> Phone (Id 1). Selebihnya identik
dengan pencarian Chat (DateRange Between, FTSLanguage "en", dsb).

`probe_search()` menjalankan satu pencarian lalu mengembalikan header + sampel
baris MENTAH untuk inspeksi kolom (Increment 1). Tidak menyimpan apa pun, tidak
mengambil audio/transkrip.
"""
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
