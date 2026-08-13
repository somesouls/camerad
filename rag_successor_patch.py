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
  3. Bila ya: menelusuri peraturan PENGGANTI terbaru lewat kolom
     status_terkait / dicabut_oleh / diubah_oleh, menarik ISI pengganti yang
     BERLAKU ke konteks, dan menyisipkan CATATAN STATUS HUKUM agar LLM tidak
     mendasarkan jawaban pada aturan yang sudah tak berlaku.

Gagal-anggun: bila bagian penelusuran error, jatuh kembali ke perilaku
'berlaku saja'.

Dipasang lewat web_app.py (import rag_successor_patch). Karena rag_engine
memakai tabel dispatch _DISPATCH yang menyimpan referensi fungsi LAMA sejak
impor, patch WAJIB memperbarui _DISPATCH["peraturan"] juga, bukan hanya
atribut modul.
"""
import rag_engine as _re


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

            # Referensi pengganti dari status_terkait (JSON), lalu fallback ke
            # kolom teks dicabut_oleh / diubah_oleh.
            terkait = _json_list(usang.get("status_terkait"))
            refs = []
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


# Pasang: ganti fungsi modul DAN entri tabel dispatch (yang menyimpan referensi
# ke fungsi lama sejak impor rag_engine).
_re._ctx_peraturan = _ctx_peraturan_tracing
try:
    _re._DISPATCH["peraturan"] = _ctx_peraturan_tracing
except Exception:
    pass

print("[rag_successor_patch] _ctx_peraturan successor-tracing aktif")
