# -*- coding: utf-8 -*-
"""rag_validity_guard_patch.py — Tahap 2: guard tata kelola masa berlaku.

Melengkapi rag_successor_patch (yang SUDAH: menandai kandidat dicabut/diubah +
menarik ISI pengganti berlaku via trace_successor) dengan satu perilaku yang
belum ada: MENDORONG abstain ketika SATU-SATUNYA dukungan peraturan yang
relevan berstatus dicabut/diubah DAN tidak ada peraturan pengganti berlaku di
basis data.

Kasus target (akar error laporan, mis. batas setor PPN PMK 39/PMK.03/2010):
pertanyaan menyasar ketentuan yang sudah dicabut; setelah data masa berlaku
dirapikan, tidak ada unit berlaku tersisa untuk ditampilkan dan tidak ada
pengganti tertaut. Tanpa guard, LLM bisa tetap menjawab dari pengetahuan umum
(tanpa menyebut nomor -> lolos dari rag_grounding_patch yang hanya menangkap
rujukan BER-NOMOR tak terdukung). Guard ini menyisipkan INSTRUKSI ABSTAIN
eksplisit ke konteks peraturan sehingga jawaban mengarah ke abstain/arahan
saluran resmi.

Cara kerja (guard PASCA-RETRIEVAL, membungkus _ctx_peraturan versi terakhir =
rag_successor_patch):
  1. Panggil _ctx_peraturan bawaan -> (teks, sumber).
  2. Deteksi kondisi target TANPA pencarian ulang: rag_successor_patch
     menyisipkan penanda "CATATAN STATUS HUKUM" HANYA saat kandidat termirip
     berstatus dicabut/diubah, dan `sumber` KOSONG hanya bila tak ada satu pun
     blok isi peraturan (baik pengganti berlaku maupun unit berlaku lain).
     Jadi (penanda ADA) DAN (sumber KOSONG) == satu-satunya dukungan berstatus
     tidak berlaku, tanpa pengganti berlaku.
  3. Bila terpenuhi -> tambahkan INSTRUKSI ABSTAIN; sumber tetap kosong.

Sumber lain (intent/sosmed/awe) TIDAK disentuh: bila ada dasar berlaku lain,
jawaban tetap bisa diberikan. Guard hanya membentuk konteks PERATURAN.

Env:
  RAG_VALIDITY_GUARD=1  aktifkan (default: NONAKTIF saat uji).

Gagal-anggun penuh: error apa pun -> hasil _ctx_peraturan bawaan dikembalikan.
Dimuat PALING AKHIR (via tail-import rag_domain_patch, sesudah nomor_pin) agar
membungkus _ctx_peraturan hasil rag_successor_patch di eval maupun produksi.
"""
import os

import rag.engine as _re

_MARKER = "CATATAN STATUS HUKUM"

_DIRECTIVE = (
    "INSTRUKSI ABSTAIN (validity guard): Ketentuan yang paling sesuai dengan "
    "pertanyaan berstatus DICABUT/DIUBAH dan TIDAK ada peraturan pengganti yang "
    "berlaku di basis data. Jangan menyusun jawaban substantif dari ketentuan "
    "yang sudah tidak berlaku, dan jangan mengarang dasar hukum. Bila tidak ada "
    "dasar hukum berlaku lain pada konteks, sampaikan bahwa jawaban terverifikasi "
    "belum tersedia dan arahkan penanya ke saluran resmi DJP."
)


def _on():
    return str(os.environ.get("RAG_VALIDITY_GUARD", "0")).strip().lower() in (
        "1", "true", "yes", "on")


_orig_ctx_peraturan = getattr(_re, "_ctx_peraturan", None)


def _ctx_peraturan_validity(q, limit=4):
    if _orig_ctx_peraturan is None:
        return "", []
    text, sources = _orig_ctx_peraturan(q, limit)
    if not _on():
        return text, sources
    try:
        if (_MARKER in (text or "")) and not sources:
            text = (text + "\n\n" if text else "") + _DIRECTIVE
            return text, []
    except Exception:
        return text, sources
    return text, sources


if _orig_ctx_peraturan is None:
    print("[rag_validity_guard] dilewati: _ctx_peraturan belum tersedia "
          "(rag_successor_patch harus dimuat lebih dulu).", flush=True)
elif _on():
    _re._ctx_peraturan = _ctx_peraturan_validity
    try:
        if isinstance(getattr(_re, "_DISPATCH", None), dict):
            _re._DISPATCH["peraturan"] = _ctx_peraturan_validity
    except Exception:
        pass
    print("[rag_validity_guard] aktif (dorong abstain bila satu-satunya dukungan "
          "peraturan berstatus dicabut/diubah tanpa pengganti berlaku).", flush=True)
else:
    print("[rag_validity_guard] nonaktif (set RAG_VALIDITY_GUARD=1 untuk mengaktifkan).",
          flush=True)
