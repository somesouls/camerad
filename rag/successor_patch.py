# -*- coding: utf-8 -*-
"""rag_successor_patch.py — successor-tracing peraturan.

Mengganti _ctx_peraturan: tetap lampirkan ISI peraturan BERLAKU; bila kandidat
termirip berstatus dicabut/diubah, telusuri pengganti (multi-hop via
peraturan_relasi) + sisipkan CATATAN STATUS HUKUM.

Fase 3 (v3): penarikan ISI pengganti memakai source_id dari langkah rantai
(bukan pencarian teks judul yang bisa nyasar ke peraturan berjudul mirip, mis.
PPh 23 royalti untuk pertanyaan PPh 21). Unit BERLAKU milik source_id ditarik
langsung via SQL (kebal gerbang cosine) lalu dipilih yang paling relevan
leksikal. Env RAG_SUCCESSOR_SID=0 -> jalur teks lama. Gagal-anggun penuh.
Dipasang lewat web_app.py (import) + memperbarui _DISPATCH["peraturan"].
"""
import rag.engine as _re
import os as _os_std
import re as _re_std


def _sid_on():
    return str(_os_std.environ.get("RAG_SUCCESSOR_SID", "1")).strip().lower() not in (
        "0", "false", "no", "off")


_STOP = {"yang", "di", "ke", "dari", "dan", "atau", "apa", "itu", "aturan",
         "diatur", "mana", "tanya", "mau", "saya", "tentang", "pada", "untuk",
         "dengan", "apakah", "bagaimana", "ada", "nya", "sih", "dong", "kah"}


def _tok(s):
    return [t for t in _re_std.findall(r"[a-z0-9]+", (s or "").lower())
            if len(t) >= 3 and t not in _STOP]


def _fetch_pengganti_by_sid(pdb, source_id, q, maks=1):
    """Ambil unit BERLAKU milik source_id via SQL (kebal gate cosine), pilih
    unit paling relevan leksikal ke query asli. Gagal-anggun: [] bila error."""
    sid = str(source_id or "").strip()
    if not sid or pdb is None:
        return []
    conn = None
    rows = []
    try:
        conn = pdb.init_db(pdb.connect())
        rows = conn.execute(
            "SELECT * FROM peraturan_unit WHERE source_id=? AND status='berlaku' "
            "LIMIT 80", (sid,)).fetchall()
    except Exception:
        rows = []
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass
    docs = []
    for r in rows:
        try:
            docs.append(r if isinstance(r, dict) else dict(r))
        except Exception:
            continue
    if not docs:
        return []
    qtok = set(_tok(q))

    def _score(d):
        blob = (str(d.get("hierarchy") or "") + " "
                + str(d.get("judul") or "") + " "
                + str(d.get("isi") or "")).lower()
        s = sum(1 for t in qtok if t in blob)
        if "tarif efektif" in blob:
            s += 3
        return s

    docs.sort(key=_score, reverse=True)
    return docs[:max(1, int(maks or 1))]


def _ctx_peraturan_tracing(q, limit=4):
    pdb = getattr(_re, "pdb", None)
    if pdb is None:
        return "", []
    _clip = _re._clip
    _json_list = _re._json_list

    blocks, sources, catatan = [], [], []
    seen = set()

    def _emit(d, tag=""):
        try:
            d = d if isinstance(d, dict) else dict(d)
        except Exception:
            return
        isi = str(d.get("isi") or "").strip()
        if not isi:
            return
        jenis = str(d.get("jenis_peraturan") or "").strip()
        nomor = str(d.get("nomor") or "").strip()
        tahun = str(d.get("tahun") or "").strip()
        pasal = str(d.get("pasal") or "").strip()
        judul = str(d.get("judul") or "").strip()
        hierarchy = str(d.get("hierarchy") or "").strip()
        reference = str(d.get("reference") or "").strip()
        source_url = str(d.get("source_url") or "").strip()
        tajuk = " ".join(x for x in [jenis, nomor,
                                     ("Tahun " + tahun) if tahun else ""] if x).strip()
        head = tajuk or judul or "Peraturan"
        if pasal:
            head += " - Pasal " + pasal
        key = head.lower()
        if key in seen:
            return
        seen.add(key)
        piece = ("Peraturan%s: " % ((" " + tag) if tag else "")) + head
        if judul and judul.lower() not in head.lower():
            piece += "\nTentang: " + _clip(judul, 200)
        piece += "\nIsi: " + _clip(isi, 700)
        blocks.append(piece)
        sources.append({"sumber": "Peraturan", "judul": head,
                        "ref": (reference or hierarchy),
                        "url": source_url})

    try:
        berlaku = pdb.search(q, limit, ("berlaku",))
    except Exception:
        berlaku = []

    try:
        probe = pdb.search(q, 3, ("berlaku", "diubah", "dicabut")) or []
        usang = None
        if probe:
            r0 = probe[0] if isinstance(probe[0], dict) else dict(probe[0])
            if str(r0.get("status") or "").lower() in ("dicabut", "diubah"):
                usang = r0
        if usang is not None:
            jenis = str(usang.get("jenis_peraturan") or "").strip()
            nomor = str(usang.get("nomor") or "").strip()
            tahun = str(usang.get("tahun") or "").strip()
            pasal = str(usang.get("pasal") or "").strip()
            judul = str(usang.get("judul") or "").strip()
            stat = str(usang.get("status") or "").strip() or "tidak berlaku"
            tajuk = " ".join(x for x in [jenis, nomor,
                                         ("Tahun " + tahun) if tahun else ""] if x).strip()
            head_usang = tajuk or judul or "Peraturan"
            if pasal:
                head_usang += " - Pasal " + pasal

            refs = []
            langkah = []
            try:
                get_tr = getattr(pdb, "trace_successor", None)
                sid_usang = str(usang.get("source_id") or "").strip()
                if callable(get_tr) and sid_usang:
                    langkah = get_tr(sid_usang, 3) or []
            except Exception:
                langkah = []
            if langkah:
                for st in langkah:
                    lab = " ".join(x for x in [str(st.get("nomor") or "").strip(),
                                               str(st.get("judul") or "").strip()] if x).strip()
                    if not lab:
                        lab = str(st.get("source_id") or "")
                    tgl = str(st.get("tanggal") or "").strip()
                    if tgl:
                        lab = "%s (%s)" % (lab, tgl)
                    if lab:
                        refs.append((lab, st))
                refs.sort(key=lambda x: 0 if str((x[1] or {}).get("status") or "").lower() == "berlaku" else 1)

            if not refs:
                terkait = _json_list(usang.get("status_terkait"))
                for it in terkait[:2]:
                    if not isinstance(it, dict):
                        continue
                    rnom = str(it.get("nomor") or "").strip()
                    rjud = str(it.get("judul") or "").strip()
                    rtgl = str(it.get("tanggal") or "").strip()
                    lab = " ".join(x for x in [rnom, rjud] if x).strip() \
                        or str(it.get("deskripsi") or "").strip()
                    if not lab:
                        continue
                    if rtgl:
                        lab = "%s (%s)" % (lab, rtgl)
                    refs.append((lab, it))
                if not refs:
                    teks = str(usang.get("dicabut_oleh") or
                               usang.get("diubah_oleh") or "").strip()
                    if teks:
                        refs.append((teks, None))

            ditarik = 0
            for lab, it in refs:
                if ditarik >= 1:
                    break
                # Fase 3: tarik ISI pengganti via source_id (akurat + kebal gate).
                sid = str(it.get("source_id") or "").strip() if isinstance(it, dict) else ""
                if sid and _sid_on():
                    for pr in _fetch_pengganti_by_sid(pdb, sid, q, maks=1):
                        before = len(blocks)
                        _emit(pr, tag="(pengganti, berlaku)")
                        if len(blocks) > before:
                            ditarik += 1
                            break
                    if ditarik >= 1:
                        break
                kunci = ""
                if isinstance(it, dict):
                    kunci = " ".join(x for x in [
                        str(it.get("jenis") or ""),
                        str(it.get("nomor") or ""),
                        str(it.get("judul") or ""),
                    ] if x).strip()
                kunci = kunci or lab
                if not kunci:
                    continue
                try:
                    pg = pdb.search(kunci, 3, ("berlaku",)) or []
                except Exception:
                    pg = []
                for pr in pg:
                    before = len(blocks)
                    _emit(pr, tag="(pengganti, berlaku)")
                    if len(blocks) > before:
                        ditarik += 1
                        break

            ref_txt = "; ".join(x[0] for x in refs) if refs else "tidak tercatat"
            if langkah:
                catatan.append(
                    "CATATAN STATUS HUKUM: Ketentuan yang paling sesuai dengan "
                    "pertanyaan, yaitu " + head_usang + ", berstatus " + stat + ". "
                    "JANGAN jadikan dasar jawaban. Rantai perubahan berurutan: "
                    + ref_txt + ". Gunakan dokumen terbaru yang berlaku di ujung "
                    "rantai tersebut."
                )
            else:
                catatan.append(
                    "CATATAN STATUS HUKUM: Ketentuan yang paling sesuai dengan "
                    "pertanyaan, yaitu " + head_usang + ", berstatus " + stat + ". "
                    "JANGAN jadikan dasar jawaban. Gunakan peraturan pengganti yang "
                    "berlaku: " + ref_txt + "."
                )
    except Exception:
        pass

    for r in (berlaku or []):
        _emit(r, tag="")

    body = list(catatan) + blocks
    return ("\n\n".join(body), sources)


_re._ctx_peraturan = _ctx_peraturan_tracing
try:
    _re._DISPATCH["peraturan"] = _ctx_peraturan_tracing
except Exception:
    pass

print("[rag_successor_patch] successor-tracing aktif (multi-hop + tarik-ISI via source_id v3)")
