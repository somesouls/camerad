"""
db/registry.py — DB Registry (Fase 1, Agentic AI Camerad)

SIFAT: ADITIF & NON-BREAKING
- Tidak mengubah perilaku endpoint /api/ask maupun /api/ask-data yang lama.
- Hanya menambah lapisan registry read-only di atas modul DB yang sudah ada.
- Database `users` DIKECUALIKAN total dari akses AI (tidak boleh di-list, di-skema,
  atau di-query).

Prinsip:
- Semua SELECT diarahkan lewat db.analytics_db.run_select (guard read-only generik:
  hanya SELECT/WITH, menolak ';', DDL, ATTACH; memaksa LIMIT).
- run_select() di modul ini TIDAK memanggil init_db() -> murni read-only
  (connect + SELECT saja).
- Domain pengetahuan glossary/disambig/intentmap berbagi file yang sama dengan
  analytics (analytics.db).
- Peraturan/SOP/Kamus memakai retrieval hybrid (FTS5 + vektor e5). Registry hanya
  mengekspos tabel KONTEN SQL-nya; tabel *_vec (embedding BLOB) & *_fts (indeks
  FTS) TIDAK didaftarkan dan tidak boleh di-SELECT.
- golden.db belum punya modul koneksi di repo -> dikecualikan pada v1.
"""

from __future__ import annotations

import importlib
from typing import Any, Dict, List, Optional

import db.analytics_db as adb

# Database yang tidak boleh diakses AI dengan alasan apa pun.
EXCLUDED = {"users"}

REGISTRY: List[Dict[str, Any]] = [
    {
        "key": "analytics",
        "label": "Analitik interaksi Dialogflow",
        "module": "db.analytics_db",
        "tables": [
            "interactions", "ingest_log", "day_status", "raw_entries",
            "candidate_status", "intent_status", "meta",
        ],
        "schema": adb.SCHEMA_FOR_LLM,
    },
    {
        "key": "agent_log",
        "label": "Log chat & kuota agent RAG",
        "module": "db.agent_log_db",
        "tables": ["rag_chat_log", "rag_quota"],
        "schema": (
            "rag_chat_log = riwayat percakapan RAG (kolom a.l. id, ts, session_id, "
            "role, content, meta). rag_quota = kuota pemakaian harian."
        ),
    },
    {
        "key": "qa",
        "label": "Indeks QA (knowledge base)",
        "module": "db.qa_index_db",
        "tables": ["qa_unit", "qa_meta"],
        "schema": (
            "qa_unit = pasangan tanya-jawab (a.l. id, question, answer, source, tags). "
            "qa_meta = metadata (key, value). "
            "CATATAN: tabel qa_vec berisi embedding BLOB, JANGAN di-SELECT untuk teks."
        ),
    },
    {
        "key": "sosmed",
        "label": "Data sosial media",
        "module": "sosmed.db",
        "tables": ["sosmed_items", "sosmed_batches", "sosmed_meta"],
        "schema": (
            "sosmed_items = item sosmed (a.l. id, batch_id, item_type, text, author, "
            "ts, meta); untuk pertanyaan gunakan WHERE item_type='pertanyaan'. "
            "sosmed_batches = batch impor. sosmed_meta = metadata (key, value)."
        ),
    },
    {
        "key": "voicebot",
        "label": "Konfigurasi voicebot",
        "module": "voicebot.config_db",
        "tables": ["vb_settings", "vb_intents", "vb_lexicon", "vb_turns"],
        "schema": (
            "vb_settings = pengaturan (key, value). vb_intents = daftar intent. "
            "vb_lexicon = leksikon/istilah. vb_turns = giliran percakapan."
        ),
    },
    {
        "key": "avaya",
        "label": "Percakapan Avaya (AWE Chat & Telepon)",
        "module": "avaya.db",
        "tables": [
            "awe_conversations", "awe_phone_interactions", "awe_runs",
            "awe_staging", "awe_day_coverage", "awe_stage_batches",
            "awe_stage_coverage", "awe_meta",
        ],
        "schema": (
            "DB AWE Avaya berisi DUA sumber terpisah: (1) CHAT live-chat dan "
            "(2) TELEPON. Kolom tanggal bertipe TEXT; untuk filter/rekap harian "
            "pakai substr(tanggal,1,10). "
            "awe_conversations = percakapan CHAT per-sid (a.l. run_id, sid, "
            "tanggal, customer, nik, agent_name, durasi, behavior "
            "['direct'/'langsung' = langsung ke agent], is_returning, "
            "mapped_intent, coverage_band, case_label, sentiment "
            "['positif'/'netral'/'negatif'], emotion, topik, jenis_layanan, "
            "deflection_gap [1=ke agent walau ada intent mirip], is_poro, "
            "non_npwp, serta skor softskill ss_salam_pembuka/ss_menanyakan_nama/"
            "ss_menyapa_customer/ss_menawarkan_bantuan/ss_hold/ss_salam_penutup/"
            "ss_lengkap [1/0]). 'reached agent' = agent_name tidak kosong. "
            "awe_phone_interactions = interaksi TELEPON per-sid (a.l. sid, day, "
            "tanggal, ani [nomor penelepon], dnis, call_id, durasi, hold_time_sec, "
            "has_audio, customer, agent_name, ringkasan, topik, jenis_layanan, "
            "sentiment, emotion, resolusi, frustrasi, analyzed_at). "
            "awe_runs = riwayat proses analisis chat. awe_meta = metadata "
            "(key, value). awe_staging/awe_day_coverage/awe_stage_batches/"
            "awe_stage_coverage = tabel staging & cakupan harian (jarang dipakai "
            "untuk analisis). CATATAN: kolom *_json (transkrip_json, analisis_json, "
            "dll) & stt_text berisi teks besar; untuk rekap pakai agregasi kolom "
            "terstruktur dan hindari SELECT kolom JSON besar tanpa alasan."
        ),
    },
    {
        "key": "glossary",
        "label": "Glosarium istilah",
        "module": "knowledge.glossary_db",
        "tables": ["glossary"],
        "schema": "glossary = istilah & definisi. Berbagi file dengan analytics.db.",
        "shared_with": "analytics",
    },
    {
        "key": "disambig",
        "label": "Disambiguasi istilah",
        "module": "knowledge.disambig_db",
        "tables": ["disambig"],
        "schema": "disambig = pemetaan istilah ambigu. Berbagi file dengan analytics.db.",
        "shared_with": "analytics",
    },
    {
        "key": "intentmap",
        "label": "Pemetaan intent",
        "module": "knowledge.intentmap_db",
        "tables": ["intentmap", "intentmap_catalog"],
        "schema": (
            "intentmap = pemetaan intent. intentmap_catalog = katalog intent. "
            "Berbagi file dengan analytics.db."
        ),
        "shared_with": "analytics",
    },
    {
        "key": "peraturan",
        "label": "Basis data peraturan perpajakan",
        "module": "peraturan.db",
        "tables": ["peraturan_unit", "peraturan_relasi", "impor_log", "peraturan_meta"],
        "schema": (
            "peraturan_unit = unit peraturan pajak per pasal/ayat/lampiran (a.l. id, "
            "jenis_peraturan, nomor, tahun, judul, bab, bagian, pasal, ayat, huruf, "
            "angka, lampiran, isi, hierarchy, status ['berlaku'/'dicabut'/'diubah'], "
            "valid_from, valid_to, topik, entitas, source_id). "
            "peraturan_relasi = relasi antar-peraturan (from_source, to_source, "
            "jenis_relasi ['penerus'/'pendahulu'], nomor_tujuan, judul_tujuan). "
            "impor_log = log impor berkas. peraturan_meta = metadata (key, value). "
            "CATATAN: pencarian teks pakai LIKE pada judul/isi; tabel peraturan_vec "
            "(BLOB) & peraturan_fts (FTS) JANGAN di-SELECT."
        ),
    },
    {
        "key": "sop",
        "label": "SOP & Proses Bisnis",
        "module": "sop.db",
        "tables": ["sop_unit", "sop_impor_log", "sop_meta"],
        "schema": (
            "sop_unit = bagian dokumen SOP/proses bisnis (a.l. id, dokumen_id, judul, "
            "kategori ['SOP'/'Proses Bisnis'/'Panduan'/'Lainnya'], bagian, urutan, "
            "isi, ringkasan, sumber_tipe, status ['aktif'], source_file, source_id). "
            "sop_impor_log = log impor berkas. sop_meta = metadata (key, value). "
            "CATATAN: pencarian teks pakai LIKE pada judul/isi; tabel sop_vec (BLOB) "
            "& sop_fts (FTS) JANGAN di-SELECT."
        ),
    },
    {
        "key": "kamus",
        "label": "Kamus sinonim/istilah (query rewriting)",
        "module": "rag.kamus_db",
        "tables": ["kamus_sinonim"],
        "schema": (
            "kamus_sinonim = pemetaan istilah baku pajak ke sinonim/variasi awam "
            "(a.l. id, istilah, sinonim [JSON array string], kategori, catatan, "
            "aktif [1=aktif, 0=nonaktif], created_at, updated_at). Dipakai untuk "
            "perluasan/penulisan ulang query."
        ),
    },
    {
        "key": "users",
        "label": "Data pengguna (DIKECUALIKAN dari AI)",
        "module": "db.users_db",
        "tables": [],
        "schema": None,
        "excluded": True,
    },
]


def _entry(key: str) -> Optional[Dict[str, Any]]:
    for d in REGISTRY:
        if d["key"] == key:
            return d
    return None


def _module(desc: Dict[str, Any]):
    return importlib.import_module(desc["module"])


def list_databases(include_excluded: bool = False) -> List[Dict[str, Any]]:
    """Daftar database terdaftar. `users` disembunyikan kecuali diminta eksplisit."""
    out: List[Dict[str, Any]] = []
    for d in REGISTRY:
        if d["key"] in EXCLUDED and not include_excluded:
            continue
        out.append({
            "key": d["key"],
            "label": d.get("label", d["key"]),
            "tables": list(d.get("tables", [])),
            "excluded": d["key"] in EXCLUDED,
            "shared_with": d.get("shared_with"),
        })
    return out


def get_schema(key: str) -> Dict[str, Any]:
    """Skema ringkas satu database untuk konteks LLM. Menolak excluded/unknown."""
    if key in EXCLUDED:
        return {"ok": False, "error": f"database '{key}' dikecualikan dari akses AI", "db": key}
    d = _entry(key)
    if d is None:
        return {"ok": False, "error": f"database '{key}' tidak dikenal", "db": key}
    return {
        "ok": True,
        "db": key,
        "label": d.get("label", key),
        "tables": list(d.get("tables", [])),
        "schema": d.get("schema"),
    }


def run_select(key: str, sql: str, max_rows: int = 200) -> Dict[str, Any]:
    """Jalankan SELECT read-only pada satu database terdaftar.

    - Menolak database excluded SEBELUM membuka koneksi.
    - Guard SQL read-only memakai db.analytics_db.run_select (SELECT/WITH saja).
    - Tidak memanggil init_db() -> murni read-only.
    """
    if key in EXCLUDED:
        return {"ok": False, "error": f"database '{key}' dikecualikan dari akses AI", "db": key}
    d = _entry(key)
    if d is None:
        return {"ok": False, "error": f"database '{key}' tidak dikenal", "db": key}
    conn = None
    try:
        conn = _module(d).connect()
        res = adb.run_select(conn, sql, max_rows=max_rows)
        if isinstance(res, dict):
            res["db"] = key
        return res
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


if __name__ == "__main__":
    dbs = list_databases()
    keys = [x["key"] for x in dbs]
    assert "users" not in keys, "users tidak boleh muncul di list_databases()"
    assert "analytics" in keys, "analytics harus ada di registry"
    print("list_databases:", keys)

    r = run_select("users", "SELECT 1")
    assert not r.get("ok", False), "run_select('users', ...) harus ditolak"
    print("run_select(users) ditolak:", r.get("error"))

    s = get_schema("analytics")
    assert s.get("ok") and s.get("schema"), "skema analytics harus tersedia"
    print("get_schema(analytics).ok:", s["ok"])

    print("REGISTRY_SMOKE_OK")
