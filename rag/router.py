# -*- coding: utf-8 -*-
"""rag_router.py — Pemetaan pertanyaan -> prioritas sumber (routing hybrid).

Strategi: aturan kata kunci cepat lebih dulu (tanpa biaya LLM). Bila sinyal
lemah/ambigu, minta LLM mengklasifikasikan domain ke salah satu dari:
  aplikasi | peraturan | umum | campuran
Keluaran: urutan prioritas sumber yang sudah disaring ke sumber yang diizinkan.

Dipakai oleh rag_engine.py sebagai Tahap 1 (Router).

Router v2 (env RAG_ROUTER2, default aktif): untuk kueri yang JELAS peraturan
(sinyal hukum kuat & sinyal aplikasi = 0), sumber lambat/kurang relevan
(default: intent, awe) DILEWATI sehingga retrieval jauh lebih cepat — mis.
memangkas sumber 'intent' (~16 dtk pada jalur agent). Penyempitan HANYA di
dalam sumber yang diizinkan (checkbox Konfigurasi tetap otoritatif; tak pernah
MENAMBAH sumber), konservatif (ambang sinyal), gagal-anggun, dan reversibel
(RAG_ROUTER2=0). Sumber yang dilewati dikembalikan pada kunci 'tunda' untuk
transparansi diagnostik.
"""
import os

try:
    import common.llm_client as llm_client
except Exception:
    llm_client = None

# Kata kunci indikatif. Sengaja pakai substring sederhana + spasi untuk
# meminimalkan false-positive pada token pendek (uu, pp, dsb).
KW_APP = (
    "aplikasi", "efin", "djp online", "djponline", "e-filing", "efiling",
    "e-billing", "ebilling", "billing", "coretax", "login", "masuk sistem",
    "password", "kata sandi", "sandi", "lupa", "reset", "error", "gagal",
    "tidak bisa", "gak bisa", "ga bisa", "akun", "email terdaftar",
    "ubah email", "ganti", "daftar", "registrasi", "aktivasi",
    "kode verifikasi", "ereg", "e-reg", "unggah", "upload", "cara ",
    "langkah", "prosedur", "tata cara", "status ",
)
KW_REG = (
    "peraturan", "undang-undang", "undang undang", " uu ", " pp ", "pmk",
    "perdirjen", "per-", "se-", "surat edaran", "pasal", "ayat",
    "ketentuan", "tarif", "dasar hukum", "sanksi", "denda", "dikecualikan",
    "dikenakan", "objek pajak", "subjek pajak", "diatur", "kewajiban",
    "batas waktu",
)

PRIORITAS = {
    "aplikasi": ["sop", "sosmed", "awe", "intent", "peraturan"],
    "peraturan": ["peraturan", "sop", "intent", "sosmed", "awe"],
    "umum": ["intent", "sosmed", "awe", "sop", "peraturan"],
    "campuran": ["sop", "peraturan", "sosmed", "awe", "intent"],
}


def _score(text, kws):
    return sum(text.count(k) for k in kws)


def _router2_on():
    return str(os.environ.get("RAG_ROUTER2", "1")).strip().lower() not in (
        "0", "false", "no", "off")


def _min_reg_signal():
    try:
        return max(1, int(os.environ.get("RAG_ROUTER2_MIN_REG", "2")))
    except Exception:
        return 2


def _router2_skip():
    """Sumber yang dilewati untuk kueri peraturan-jelas (override via
    RAG_ROUTER2_SKIP, dipisah koma; kosongkan untuk nonaktifkan penyempitan)."""
    raw = os.environ.get("RAG_ROUTER2_SKIP")
    if raw is None:
        return {"intent", "awe"}
    return {x.strip().lower() for x in raw.split(",") if x.strip()}


def _llm_domain(q):
    if llm_client is None:
        return None
    try:
        sys = (
            "Klasifikasikan pertanyaan pengguna ke SATU kategori dan jawab "
            "HANYA satu kata: aplikasi, peraturan, umum, campuran.\n"
            "- aplikasi: cara pakai aplikasi pajak, akun, EFIN, error, "
            "prosedur layanan.\n"
            "- peraturan: dasar hukum, pasal, tarif, ketentuan, sanksi.\n"
            "- campuran: menyentuh keduanya.\n"
            "- umum: sapaan atau tidak jelas."
        )
        out = llm_client.chat(
            [{"role": "user", "content": q or ""}],
            system=sys, max_new_tokens=8, temperature=0.0,
        )
        out = (out or "").strip().lower()
        for k in ("campuran", "peraturan", "aplikasi", "umum"):
            if k in out:
                return k
    except Exception:
        return None
    return None


def route(q, allowed=None):
    """Kembalikan dict {ordered, domain, metode, tunda}.

    - ordered : daftar kunci sumber terurut prioritas, hanya yang diizinkan.
    - domain  : aplikasi|peraturan|umum|campuran.
    - metode  : 'kata-kunci' / 'llm' / '...+router2' bila penyempitan aktif.
    - tunda   : sumber yang sengaja dilewati Router v2 (bisa kosong).
    """
    allowed = [s for s in (allowed or []) if s] or list(PRIORITAS["campuran"])
    text = " " + (q or "").lower() + " "
    sa, sr = _score(text, KW_APP), _score(text, KW_REG)
    metode = "kata-kunci"
    if sr > sa and sr > 0:
        domain = "peraturan"
    elif sa > sr and sa > 0:
        domain = "aplikasi"
    else:
        d = _llm_domain(q)
        domain = d or "umum"
        metode = "llm" if d else "kata-kunci(default)"
    order = [s for s in PRIORITAS.get(domain, PRIORITAS["campuran"]) if s in allowed]
    for s in allowed:
        if s not in order:
            order.append(s)

    # Router v2 — penyempitan sumber adaptif-kueri untuk kueri JELAS peraturan.
    # Hanya menyempitkan di dalam `allowed` (tak pernah menambah), konservatif
    # (sinyal hukum kuat & aplikasi nol), gagal-anggun, reversibel.
    tunda = []
    try:
        if (_router2_on() and domain == "peraturan"
                and sr >= _min_reg_signal() and sa == 0):
            lewati = _router2_skip()
            inti = [s for s in order if s not in lewati]
            # Jaga mutu: hanya sempitkan bila sumber utama masih tersisa.
            if lewati and any(s in inti for s in ("peraturan", "sop", "sosmed")):
                dilewati = [s for s in order if s in lewati]
                if dilewati:
                    order = inti
                    tunda = dilewati
                    metode = metode + "+router2"
    except Exception:
        tunda = []

    return {"ordered": order, "domain": domain, "metode": metode,
            "skor_aplikasi": sa, "skor_peraturan": sr, "tunda": tunda}
