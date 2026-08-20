# -*- coding: utf-8 -*-
"""finetune/build_grounded.py — Dataset #3: RAG-grounded SFT (peraturan & SOP).

Menjawab kebingungan #3 (peraturan/SOP tak punya pertanyaan): JANGAN menghafal
pasal ke bobot. Latih pola KONSUMEN-RAG: {pertanyaan + konteks yang diambil} ->
{jawaban grounded + sitasi}. Peraturan/SOP tetap di RAG (retrieval); LoRA hanya
belajar CARA menjawab dari konteks dengan disiplin sitasi & anti-ngarang.

Sumber korpus:
  * peraturan.db -> tabel peraturan_unit (status='berlaku')
  * sop.db       -> tabel sop_unit (status='aktif')

Pertanyaan disintesis dari judul/identitas unit (template deterministik). Untuk
mutu lebih tinggi, generator pertanyaan berbasis LLM bisa dipasang belakangan
(lihat finetune/README.md).

Jalankan:  python -m finetune.build_grounded
"""
import os
import sys
import hashlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finetune import common as C  # noqa: E402

_Q_TMPL = [
    "Apa ketentuan yang diatur dalam %s?",
    "Jelaskan isi %s.",
    "Menurut %s, bagaimana aturannya?",
    "Apa yang diatur pada %s?",
]


def _pick(seed_text, options):
    h = int(hashlib.sha1((seed_text or "x").encode("utf-8")).hexdigest(), 16)
    return options[h % len(options)]


def _cite_peraturan(r):
    jn = C.clean(r.get("jenis_peraturan"))
    no = C.clean(r.get("nomor"))
    th = r.get("tahun")
    head = " ".join(x for x in [jn, no] if x)
    if th:
        head = (head + " Tahun %s" % th).strip()
    tail = []
    if C.clean(r.get("pasal")):
        tail.append("Pasal %s" % C.clean(r.get("pasal")))
    if C.clean(r.get("ayat")):
        tail.append("ayat (%s)" % C.clean(r.get("ayat")))
    label = " ".join([x for x in [head, " ".join(tail)] if x]).strip()
    return label or (C.clean(r.get("judul")) or "peraturan terkait")


def _cite_sop(r):
    jd = C.clean(r.get("judul")) or "Dokumen SOP"
    bg = C.clean(r.get("bagian"))
    kat = C.clean(r.get("kategori")) or "SOP"
    label = jd + (" — %s" % bg if bg else "")
    return "%s (%s)" % (label, kat)


def _emit(samples, cite, isi, subject, meta):
    isi = C.clean(isi)
    if not isi:
        return
    q = _pick(cite + (subject or ""), _Q_TMPL) % (subject or cite)
    ans = isi if len(isi) <= 1400 else (isi[:1400].rstrip() + " …")
    ans = ans + "\n\nDasar: " + cite + "."
    user = "Konteks:\n[%s]\n%s\n\nPertanyaan: %s" % (cite, isi, q)
    samples.append(C.sample(
        [{"role": "system", "content": C.SYS_GROUNDED},
         {"role": "user", "content": user},
         {"role": "assistant", "content": ans}], meta))


def build(limit_peraturan=8000, limit_sop=5000, min_len=80):
    samples = []
    # --- peraturan ---
    try:
        from peraturan import db as pdb
        c = pdb.init_db(pdb.connect())
        try:
            rows = c.execute(
                "SELECT jenis_peraturan, nomor, tahun, judul, hierarchy, pasal, "
                "ayat, isi FROM peraturan_unit WHERE status='berlaku' "
                "AND isi IS NOT NULL LIMIT ?", (int(limit_peraturan),)).fetchall()
        except Exception:
            rows = []
        for r in rows:
            r = dict(r)
            if len(C.clean(r.get("isi"))) < min_len:
                continue
            cite = _cite_peraturan(r)
            subject = C.clean(r.get("judul")) or cite
            _emit(samples, cite, r.get("isi"), subject,
                  {"task": "grounded", "source": "peraturan"})
        c.close()
    except Exception as e:
        print("[grounded] peraturan dilewati:", e)
    # --- sop ---
    try:
        from sop import db as sdb
        c = sdb.init_db(sdb.connect())
        try:
            rows = c.execute(
                "SELECT judul, bagian, kategori, isi FROM sop_unit "
                "WHERE status='aktif' AND isi IS NOT NULL LIMIT ?",
                (int(limit_sop),)).fetchall()
        except Exception:
            rows = []
        for r in rows:
            r = dict(r)
            if len(C.clean(r.get("isi"))) < min_len:
                continue
            cite = _cite_sop(r)
            subject = C.clean(r.get("judul")) or cite
            _emit(samples, cite, r.get("isi"), subject,
                  {"task": "grounded", "source": "sop"})
        c.close()
    except Exception as e:
        print("[grounded] sop dilewati:", e)
    return samples


def main():
    s = build()
    info = C.write_jsonl("grounded.jsonl", s, val_ratio=0.05)
    print("[grounded] sampel:", len(s), "->", info)
    return info


if __name__ == "__main__":
    main()
