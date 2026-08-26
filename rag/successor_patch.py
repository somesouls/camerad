# -*- coding: utf-8 -*-
"""
rag_successor_patch.py
----------------------
Poin #1 arsitektur RAG final: "successor-tracing" peraturan.

Mesin lama (rag_engine._ctx_peraturan) HANYA melampirkan peraturan berstatus
'berlaku' dan MEMBUANG diam-diam peraturan 'dicabut'/'diubah'. Akibatnya, bila
ketentuan yang PALING cocok dengan pertanyaan justru sudah dicabut/diubah,
mesin tidak memberi tahu apa pun dan bisa menjawab dari peraturan berlaku lain
yang kurang relevan.

Patch ini mengganti _ctx_peraturan sehingga:
  1. Tetap hanya melampirkan ISI peraturan yang BERLAKU (aman, tak berubah).
  2. Melakukan probe lintas-status untuk mendeteksi apakah kandidat TERMIRIP
     justru berstatus dicabut/diubah.
  3. Bila ya: menelusuri peraturan PENGGANTI terbaru, menarik ISI pengganti
     yang BERLAKU ke konteks, dan menyisipkan CATATAN STATUS HUKUM agar LLM
     tidak mendasarkan jawaban pada aturan yang sudah tak berlaku.

Fase 2 (v2): penelusuran pengganti menjadi MULTI-HOP bila tabel
peraturan_relasi sudah dibangun (phase2_upgrade) — rantai A(dicabut) ->
B(diubah) -> C(berlaku) ditelusuri sampai ujung yang berlaku, dan penarikan
ISI diprioritaskan dari dokumen ujung yang berlaku. Bila tabel relasi belum
ada/kosong, perilaku kembali ke jalur JSON 1-lompatan (status_terkait /
dicabut_oleh) persis seperti v1.

Fase 3 (v3): penarikan ISI pengganti kini memakai source_id dari langkah
rantai — bukan pencarian teks judul yang bisa nyasar ke peraturan lain
berjudul mirip (mis. PPh 23 royalti untuk pertanyaan PPh 21). Unit BERLAKU
milik source_id itu diambil langsung via SQL (kebal gerbang cosine), lalu
dipilih unit yang paling relevan secara leksikal ke pertanyaan. Env
RAG_SUCCESSOR_SID=0 mengembalikan ke jalur pencarian teks lama.

Gagal-anggun: bila bagian penelusuran error, jatuh kembali ke perilaku
'berlaku saja'.

Dipasang lewat web_app.py (import rag_successor_patch). Karena rag_engine
memakai tabel dispatch _DISPATCH yang menyimpan referensi fungsi LAMA sejak
impor, patch WAJIB memperbarui _DISPATCH["peraturan"] juga, bukan hanya
atribut modul.
"""
import rag.engine as _re
import os as _os_std
import re as _re_std


def _sid_on():
    """Fase 3 aktif? RAG_SUCCESSOR_SID=0 -> kembali ke penarikan via teks (lama)."""
    return str(_os_std.environ.get("RAG_SUCCESSOR_SID", "1")).strip().lower() not in (
        "0", "false", "no", "off")


_STOP = {"yang", "di", "ke", "dari", "dan", "atau", "apa", "itu", "aturan",
         "diatur", "mana", "tanya", "mau", "saya", "tentang", "pada", "untuk",
         "dengan", "apakah", "bagaimana", "ada", "nya", "sih", "dong", "kah"}


def _tok(s):
    return [t for t in _re_std.findall(r"[a-z0-9]+", (s or "").lower())
            if len(t) >= 3 and t not in _STOP]


def _fetch_pengganti_by_sid(pdb, source_id, q, maks=1):
    """Ambil unit BERLAKU milik source_id LANGSUNG via SQL (kebal gerbang
    cosine), lalu pilih unit paling relevan secara leksikal ke query asli.

    Rantai successor sudah memastikan dokumen pengganti yang tepat (punya
    source_id); menariknya via source_id jauh lebih akurat daripada mencari
    ulang lewat teks judul yang kerap nyasar ke peraturan lain berjudul mirip.
    Gagal-anggun penuh: kembalikan [] bila apa pun error.
    """
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

    # (1) Konteks utama: hanya BERLAKU (perilaku aman lama).
    try:
        berlaku = pdb.search(q, limit, ("berlaku",))
    except Exception:
        berlaku = []

    # (2)+(3) Deteksi kandidat termirip yang tak berlaku, lalu telusuri
    #         penggantinya. Dibungkus agar gagal-anggun ke 'berlaku saja'.
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

            # Fase 2: coba telusur MULTI-HOP lewat tabel peraturan_relasi dulu
            # (dibangun phase2_upgrade). Rantai A(dicabut) -> B(diubah) ->
            # C(berlaku) ditelusuri sampai ujung. Bila tabel relasi belum ada /
            # kosong -> fallback ke jalur JSON 1-lompatan (perilaku v1).
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
                # Prioritaskan penarikan ISI dari dokumen berstatus 'berlaku'
                # (ujung rantai), bukan perantara yang masih diubah/dicabut.
                refs.sort(key=lambda x: 0 if str((x[1] or {}).get("status") or "").lower() == "berlaku" else 1)

            if not refs:
                # Referensi pengganti dari status_terkait (JSON), lalu fallback
                # ke kolom teks dicabut_oleh / diubah_oleh.
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

            # Tarik ISI peraturan pengganti yang BERLAKU (maks 1) ke konteks.
            ditarik = 0
            for lab, it in refs:
                if ditarik >= 1:
                    break
                # Fase 3: bila step rantai punya source_id, tarik ISI pengganti
                # LANGSUNG via source_id (akurat + kebal gate cosine). Jalur
                # teks (lama) sering nyasar ke peraturan lain berjudul mirip.
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

    # Lampirkan konteks BERLAKU utama (setelah pengganti; dedup via 'seen').
    for r in (berlaku or []):
        _emit(r, tag="")

    body = list(catatan) + blocks
    return ("\n\n".join(body), sources)


# Pasang: ganti fungsi modul DAN entri