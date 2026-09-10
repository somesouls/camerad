# -*- coding: utf-8 -*-
"""data_dictionary.py — Grounding pengetahuan untuk Tanya AI.

Menyatukan tiga lapis 'bekal' agar LLM tidak buta saat menulis SQL:
  A. KAMUS NILAI (value catalog): nilai NYATA kolom kategorikal, diambil LIVE
     dari DB (cached), supaya model memakai nilai yang benar-benar ada
     (mis. sentiment='negatif', behavior=<nilai asli>) alih-alih menebak.
  B. DESKRIPSI KOLOM (data dictionary): arti tiap kolom penting + catatan relasi.
  D. CONTOH (few-shot): pasangan pertanyaan->SQL per domain sebagai teladan.

Semua READ-ONLY & ADITIF. Nilai kategorikal diambil lewat db.registry.run_select
(guard read-only, database `users` dikecualikan). Gagal-anggun: bila DB tidak
tersedia, fungsi mengembalikan teks kosong tanpa menggagalkan permintaan.

Dipakai oleh knowledge/ask_precise_routes.py (disuntik ke prompt) dan
knowledge/agentic.py (dilampirkan pada observasi aksi 'schema').
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Tuple

import db.registry as registry

# --- Konfigurasi ---------------------------------------------------------
_MAX_VALUES = 40          # maksimum nilai distinct per kolom yang ditampilkan
_TTL_CACHE = None         # TTL cache nilai (detik); di-lazy dari env


def _ttl() -> int:
    global _TTL_CACHE
    if _TTL_CACHE is None:
        try:
            _TTL_CACHE = int(os.environ.get("AI_VALUE_CATALOG_TTL", "3600"))
        except Exception:
            _TTL_CACHE = 3600
    return _TTL_CACHE


# --- B. DESKRIPSI KOLOM --------------------------------------------------
COLUMN_DESCRIPTIONS: Dict[str, Dict[str, Dict[str, str]]] = {
    "avaya": {
        "awe_conversations": {
            "sid": "ID unik percakapan CHAT.",
            "tanggal": "Tanggal/waktu (TEXT). Filter harian: substr(tanggal,1,10).",
            "customer": "NAMA pelanggan (IDENTITAS, BUKAN isi percakapan).",
            "nik": "NIK pelanggan (identitas).",
            "agent_name": "Nama agen. KOSONG/NULL = percakapan bot-only (tak pernah ke agen).",
            "behavior": "Pola masuk percakapan (mis. langsung ke agen vs coba bot dulu).",
            "mapped_intent": "Intent hasil pemetaan.",
            "coverage_band": "Pita cakupan penanganan.",
            "case_label": "Label hasil kasus (mis. terlayani bot / eskalasi).",
            "sentiment": "Sentimen percakapan.",
            "emotion": "Emosi terdeteksi.",
            "topik": "Topik percakapan.",
            "jenis_layanan": "Jenis layanan.",
            "deflection_gap": "1 = tetap ke agen walau ada intent mirip.",
            "is_returning": "1 = pelanggan berulang.",
            "transkrip_json": "ISI percakapan CHAT (TEXT JSON [{role,text}]). Cari pola pakai REGEXP atau aksi content_search; JANGAN di-SELECT untuk agregasi.",
        },
        "awe_phone_interactions": {
            "sid": "ID interaksi TELEPON.",
            "tanggal": "Tanggal/waktu (TEXT). Filter harian: substr(tanggal,1,10).",
            "ani": "Nomor penelepon (identitas).",
            "agent_name": "Nama agen.",
            "resolusi": "Status penyelesaian panggilan.",
            "sentiment": "Sentimen.",
            "topik": "Topik.",
            "jenis_layanan": "Jenis layanan.",
            "stt_text": "ISI panggilan hasil speech-to-text (teks). Cari pola pakai REGEXP atau content_search.",
            "transkrip_json": "Transkrip TELEPON terstruktur (bila ada).",
            "ringkasan": "Ringkasan panggilan.",
        },
    },
    "analytics": {
        "interactions": {
            "day": "Tanggal YYYY-MM-DD (Asia/Jakarta). Pakai ini untuk filter tanggal.",
            "user_phrase": "Pertanyaan user (ISI).",
            "bot_response": "Jawaban bot (ISI).",
            "intent_name": "Nama intent yang match.",
            "is_fallback": "1 = pertanyaan tak dikenali (fallback).",
            "is_system": "1 = intent sistem (welcome/hubungi agent).",
            "session_id": "ID percakapan.",
            "lang": "Bahasa ('id'/'en').",
            "score": "Confidence 0..1.",
        },
    },
    "sosmed": {
        "sosmed_items": {
            "item_type": "Jenis item; untuk pertanyaan gunakan item_type='pertanyaan'.",
            "text": "ISI teks item sosmed.",
            "author": "Penulis.",
            "ts": "Waktu.",
            "batch_id": "ID batch impor.",
        },
    },
}


# --- A. Kolom kategorikal yang nilainya diambil LIVE ---------------------
CATEGORICAL: Dict[str, List[Tuple[str, str]]] = {
    "avaya": [
        ("awe_conversations", "behavior"),
        ("awe_conversations", "case_label"),
        ("awe_conversations", "coverage_band"),
        ("awe_conversations", "sentiment"),
        ("awe_conversations", "emotion"),
        ("awe_conversations", "jenis_layanan"),
        ("awe_conversations", "mapped_intent"),
        ("awe_phone_interactions", "resolusi"),
        ("awe_phone_interactions", "sentiment"),
        ("awe_phone_interactions", "jenis_layanan"),
    ],
    "analytics": [
        ("interactions", "lang"),
    ],
    "sosmed": [
        ("sosmed_items", "item_type"),
    ],
}


# --- D. Few-shot contoh pertanyaan -> SQL --------------------------------
FEWSHOT: Dict[str, List[Dict[str, str]]] = {
    "avaya": [
        {"q": "Berapa percakapan chat hari ini?",
         "sql": "SELECT COUNT(*) FROM awe_conversations WHERE substr(tanggal,1,10)='<HARI_INI>'"},
        {"q": "Percakapan bot-only bulan ini yang sentimennya negatif",
         "sql": "SELECT sid, customer AS nama, nik FROM awe_conversations WHERE (agent_name IS NULL OR agent_name='') AND sentiment='negatif' AND substr(tanggal,1,7)='<BULAN_INI>' LIMIT 200"},
        {"q": "Cari isi percakapan yang menyebut email @gmail.com dengan >1 titik sebelum @ (kecualikan bot-only)",
         "sql": r"-- terbaik: aksi content_search (mode agentic). Alternatif SQL: SELECT sid, customer AS nama, nik FROM awe_conversations WHERE agent_name IS NOT NULL AND agent_name<>'' AND transkrip_json REGEXP '[A-Za-z0-9_%+-]+(?:\.[A-Za-z0-9_%+-]+){2,}@gmail\.com' LIMIT 200"},
    ],
    "analytics": [
        {"q": "Pertanyaan fallback terbanyak minggu ini",
         "sql": "SELECT user_phrase, COUNT(*) AS n FROM interactions WHERE is_fallback=1 AND day>='<7HARI_LALU>' GROUP BY lower(user_phrase) ORDER BY n DESC LIMIT 20"},
        {"q": "Volume interaksi per hari bulan ini",
         "sql": "SELECT day, COUNT(*) AS n FROM interactions WHERE substr(day,1,7)='<BULAN_INI>' GROUP BY day ORDER BY day"},
    ],
    "sosmed": [
        {"q": "Pertanyaan sosmed yang menyebut 'efin'",
         "sql": "SELECT id, author, ts FROM sosmed_items WHERE item_type='pertanyaan' AND lower(text) LIKE '%efin%' LIMIT 50"},
    ],
}


# --- Cache in-memory untuk value catalog --------------------------------
_cache: Dict[str, Dict[str, Any]] = {}  # db_key -> {"ts": float, "data": {...}}


def value_catalog(db_key: str, refresh: bool = False) -> Dict[str, Dict[str, List[Tuple[str, Any]]]]:
    """Ambil nilai NYATA kolom kategorikal (top-N per kolom) dari DB, cached.

    Return: {table: {col: [(nilai, jumlah), ...]}}. Kosong bila DB tak tersedia.
    """
    key = (db_key or "").strip()
    cols = CATEGORICAL.get(key)
    if not cols:
        return {}
    now = time.time()
    ent = _cache.get(key)
    if ent and not refresh and _ttl() > 0 and (now - ent.get("ts", 0)) < _ttl():
        return ent.get("data", {})
    data: Dict[str, Dict[str, List[Tuple[str, Any]]]] = {}
    for table, col in cols:
        sql = (
            'SELECT "{c}" AS v, COUNT(*) AS n FROM "{t}" '
            "WHERE \"{c}\" IS NOT NULL AND \"{c}\" <> '' "
            'GROUP BY "{c}" ORDER BY n DESC LIMIT {n}'
        ).format(c=col, t=table, n=_MAX_VALUES + 1)
        try:
            res = registry.run_select(key, sql, max_rows=_MAX_VALUES + 1)
        except Exception:
            res = {"ok": False}
        if not res.get("ok"):
            continue
        vals: List[Tuple[str, Any]] = []
        for row in (res.get("rows") or [])[:_MAX_VALUES]:
            try:
                v = row[0]
                n = row[1] if len(row) > 1 else None
            except Exception:
                continue
            if v is None or str(v).strip() == "":
                continue
            vals.append((str(v), n))
        if vals:
            data.setdefault(table, {})[col] = vals
    _cache[key] = {"ts": now, "data": data}
    return data


def _fmt_values(data: Dict[str, Dict[str, List[Tuple[str, Any]]]]) -> List[str]:
    lines: List[str] = []
    for table, cols in data.items():
        for col, vals in cols.items():
            shown = ", ".join(
                ("%s (%s)" % (v, n) if n is not None else str(v)) for v, n in vals)
            lines.append("- %s.%s: %s" % (table, col, shown))
    return lines


def grounding_text(db_key: str, max_chars: int = 4000) -> str:
    """Blok teks grounding gabungan (deskripsi kolom + nilai nyata + contoh).

    Aman dipanggil kapan saja; mengembalikan '' bila tak ada bekal untuk db_key.
    """
    key = (db_key or "").strip()
    parts: List[str] = []

    desc = COLUMN_DESCRIPTIONS.get(key)
    if desc:
        parts.append("Deskripsi kolom penting:")
        for table, cols in desc.items():
            for col, d in cols.items():
                parts.append("- %s.%s: %s" % (table, col, d))

    try:
        data = value_catalog(key)
    except Exception:
        data = {}
    if data:
        parts.append("")
        parts.append(
            "Nilai NYATA kolom kategorikal (pakai PERSIS salah satu nilai ini bila "
            "memfilter; JANGAN mengarang nilai; format 'nilai (jumlah)'):")
        parts.extend(_fmt_values(data))

    fs = FEWSHOT.get(key)
    if fs:
        parts.append("")
        parts.append(
            "Contoh pertanyaan -> SQL (placeholder <...> ganti sesuai tanggal hari ini):")
        for ex in fs:
            parts.append("Q: " + ex.get("q", ""))
            parts.append("SQL: " + ex.get("sql", ""))

    if not parts:
        return ""
    text = "\n\n=== KAMUS DATA & CONTOH (grounding, READ-ONLY) ===\n" + "\n".join(parts)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\u2026(dipotong)"
    return text


if __name__ == "__main__":
    # Smoke test aman-offline: struktur konstanta & pembentukan teks.
    assert "avaya" in COLUMN_DESCRIPTIONS
    assert any(t == "awe_conversations" for t in COLUMN_DESCRIPTIONS["avaya"])
    assert "avaya" in CATEGORICAL and CATEGORICAL["avaya"], "kategorikal avaya harus ada"
    assert "avaya" in FEWSHOT and FEWSHOT["avaya"], "few-shot avaya harus ada"
    # grounding_text mungkin memanggil DB; harus tetap mengembalikan string.
    g = grounding_text("avaya")
    assert isinstance(g, str)
    print("DATA_DICTIONARY_SMOKE_OK len(grounding avaya)=", len(g))
