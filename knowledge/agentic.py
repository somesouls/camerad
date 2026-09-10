# -*- coding: utf-8 -*-
"""agentic.py — Tanya AI 'agentic' (Fase 2).

Loop agentic READ-ONLY di atas DB Registry (db/registry.py):
- LLM diberi katalog database + skema ringkas (per permintaan), lalu memilih
  satu database dan menulis SATU query SELECT per langkah.
- Hasil query (observasi) dikembalikan ke LLM; loop berlanjut sampai LLM
  menjawab final atau mencapai batas langkah.
- SEMUA guardrail Fase 1 tetap berlaku lewat db.registry.run_select:
  read-only (SELECT/WITH saja), satu statement (tanpa ';'), LIMIT dipaksa,
  DDL/ATTACH ditolak, dan database `users` DIKECUALIKAN total.

v2 (retrieval basis pengetahuan): loop kini juga dapat menelusuri sumber
TEKSTUAL (peraturan, SOP, media sosial, percakapan AWE, intent) lewat aksi
'rag_search' yang membungkus mesin retrieval RAG (rag/kb_search.py) TANPA LLM
sintesis. Tetap READ-ONLY.

v3 (grounding & pencarian isi): aksi 'columns' (introspeksi kolom NYATA via
registry.get_columns) dan 'content_search' (pencocokan POLA regex pada ISI di
sisi server via knowledge/content_search.py — kini lintas-DB). Query gagal karena
'no such column' otomatis diberi daftar kolom nyata + petunjuk isi percakapan.

v4 (kamus data & batas via .env): aksi 'schema' kini melampirkan KAMUS DATA dari
knowledge/data_dictionary.py (deskripsi kolom, nilai kategorikal NYATA, contoh)
sehingga model memilih filter dengan benar. Batas langkah dapat diatur lewat
environment: AGENTIC_MAX_QUERY_STEPS dan AGENTIC_MAX_ITERS.

Sifat: ADITIF & NON-BREAKING. Modul & endpoint baru; /api/ask dan
/api/ask-data lama tidak diubah perilakunya.
"""
import os
import json
import re
import datetime as _dt

import db.registry as registry
import common.llm_client as llm_client
import common.pii_mask as pii_mask

try:
    from knowledge import ctx as kctx  # konteks silang pustaka (opsional)
except Exception:  # pragma: no cover - kctx opsional
    kctx = None

try:
    import rag.kb_search as rag_kb  # retrieval basis pengetahuan (opsional)
except Exception:  # pragma: no cover - retrieval KB opsional
    rag_kb = None

try:
    import knowledge.content_search as content_search  # pencarian ISI (opsional)
except Exception:  # pragma: no cover - content_search opsional
    content_search = None

try:
    import knowledge.data_dictionary as data_dict  # kamus data/grounding (opsional)
except Exception:  # pragma: no cover - grounding opsional
    data_dict = None


def _env_int(name, default):
    try:
        v = int(os.environ.get(name, "") or default)
        return v if v > 0 else default
    except Exception:
        return default


# --- Batasan aman (guardrail operasional) --------------------------------
# Dapat diatur lewat .env: AGENTIC_MAX_QUERY_STEPS, AGENTIC_MAX_ITERS.
MAX_QUERY_STEPS = _env_int("AGENTIC_MAX_QUERY_STEPS", 8)  # batas langkah query/pencarian aktual
MAX_ITERS = _env_int("AGENTIC_MAX_ITERS", MAX_QUERY_STEPS + 6)  # total giliran model
MAX_ROWS = 200           # baris maksimum per query (diteruskan ke run_select)
MAX_ROWS_TO_LLM = 50     # baris yang diumpankan balik ke LLM per observasi
MAX_RESULT_CHARS = 3500  # batas ukuran teks observasi yang diumpan balik
MAX_SCHEMA_CHARS = 8000  # batas observasi 'schema' (memuat kamus data)
MAX_RAG_STEPS = 4        # batas langkah pencarian basis pengetahuan (rag_search)
MAX_RAG_CHARS = 3500     # batas panjang konteks RAG yang diumpan balik ke LLM


def _now():
    return _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _today_jkt():
    """Tanggal hari ini zona Asia/Jakarta (fallback UTC+7)."""
    try:
        from zoneinfo import ZoneInfo
        return _dt.datetime.now(ZoneInfo("Asia/Jakarta")).date()
    except Exception:
        tz = _dt.timezone(_dt.timedelta(hours=7))
        return _dt.datetime.now(tz).date()


def _query_hints():
    """Petunjuk umum: sadar tanggal + fuzzy identitas + pemisahan identitas vs ISI."""
    today = _today_jkt().isoformat()
    return (
        "\n\nKonteks waktu: hari ini = " + today + " (zona Asia/Jakarta). "
        "Pertanyaan relatif ('hari ini', 'kemarin', 'minggu ini', 'bulan ini', "
        "'30 hari terakhir') dihitung dari tanggal itu. Bila kolom tanggal TEXT, "
        "pakai substr(kolom,1,10) untuk filter harian.\n"
        "Pencarian IDENTITAS/teks kolom (nama pelanggan/customer, SID, nomor "
        "telepon/ANI, nama agen, NIK, topik, intent): gunakan pencocokan SEBAGIAN "
        "& tidak peka huruf, mis. WHERE lower(customer) LIKE lower('%kata%'); "
        "hindari '=' untuk nama/teks kecuali nilainya jelas eksak.\n"
        "PENTING — IDENTITAS vs ISI PERCAKAPAN: customer/ani/nik/agent_name adalah "
        "IDENTITAS, BUKAN isi. ISI/teks percakapan (yang diketik/diucapkan, termasuk "
        "alamat email yang diketik) ada di kolom TRANSKRIP: transkrip_json (CHAT) dan "
        "stt_text/transkrip_json (TELEPON). TIDAK ADA kolom conversation_text/"
        "isi_percakapan/transcript — JANGAN mengarang; bila ragu pakai aksi 'columns' "
        "atau 'schema' (memuat kamus data) untuk melihat kolom & nilai nyata. Bila "
        "pengguna meminta pencarian pada ISI percakapan, JANGAN cari di customer; "
        "pakai aksi 'content_search' (paling andal, cocok di sisi server) atau REGEXP "
        "pada kolom transkrip. Fungsi REGEXP tersedia (case-insensitive); contoh "
        "email @gmail.com dengan LEBIH DARI SATU titik sebelum @ (mis. "
        "sam.sul.h@gmail.com; wp1@gmail.com atau nico.reno@gmail.com yang 0/1 titik "
        "TIDAK dicari): "
        r"'[A-Za-z0-9_%+-]+(?:\.[A-Za-z0-9_%+-]+){2,}@gmail\.com'"
        ". 'bot-only' (murni bot) = agent_name kosong; untuk mengecualikan bot-only "
        "tambahkan AND agent_name IS NOT NULL AND agent_name<>'' (atau pakai "
        "exclude_bot_only pada content_search). Bila sebuah query mengembalikan 0 "
        "baris, coba longgarkan (LIKE lebih longgar / lepas filter tanggal / periksa "
        "nilai kategorikal di kamus data) sebelum menyimpulkan data tidak ada."
    )


def _clip(s, n=MAX_RESULT_CHARS):
    s = s or ""
    return s if len(s) <= n else (s[:n] + "\u2026(dipotong)")


def _catalog_text():
    """Teks katalog database (key, label, tabel) untuk konteks LLM."""
    lines = []
    for d in registry.list_databases():
        tbl = ", ".join(d.get("tables") or []) or "-"
        lines.append("- %s (%s) | tabel: %s" % (
            d.get("key"), d.get("label", d.get("key")), tbl))
    return "\n".join(lines) if lines else "(tidak ada database terdaftar)"


def _system_prompt():
    rag_line = ""
    if rag_kb is not None:
        rag_line = (
            "- Telusuri BASIS PENGETAHUAN tekstual (peraturan, SOP, media sosial, "
            "percakapan AWE, intent) bila butuh dasar hukum/prosedur/narasi yang "
            "tidak ada di database terstruktur: "
            "{\"action\":\"rag_search\",\"query\":\"kata kunci\",\"sources\":[\"peraturan\",\"sop\"]}.\n"
            "  * 'sources' opsional (subset: intent, awe, sosmed, peraturan, sop); "
            "kosongkan untuk mencari semua sumber. READ-ONLY.\n"
        )
    cs_line = ""
    if content_search is not None:
        try:
            _supported = ", ".join(content_search.supported())
        except Exception:
            _supported = "avaya"
        cs_line = (
            "- Cari POLA pada ISI percakapan (regex, DICOCOKKAN DI SISI SERVER, hanya "
            "mengembalikan kolom identitas aman spt sid/nama/nik): "
            "{\"action\":\"content_search\",\"db\":\"avaya\",\"pattern\":\"<regex>\","
            "\"scope\":\"chat\",\"exclude_bot_only\":true,\"prefilter\":\"gmail.com\"}.\n"
            "  * Pakai ini bila pertanyaan menyangkut ISI/teks percakapan (mis. "
            "mencari alamat email/kata pada transkrip), BUKAN identitas. Didukung "
            "untuk DB: " + _supported + ". Untuk avaya 'scope'='chat'/'phone'; DB lain "
            "pakai scope default. 'prefilter' opsional (substring mempersempit "
            "kandidat). READ-ONLY.\n"
        )
    return (
        "Kamu asisten data internal Camerad untuk tim analis DJP. Kamu menjawab "
        "pertanyaan dengan MENELUSURI beberapa database internal (READ-ONLY).\n\n"
        "Cara kerja (WAJIB):\n"
        "- Balas TEPAT SATU objek JSON per langkah. Tanpa teks lain, tanpa markdown.\n"
        "- Lihat skema + KAMUS DATA (deskripsi kolom, nilai kategorikal NYATA, contoh) "
        "sebelum menulis query: {\"action\":\"schema\",\"db\":\"<key>\"}.\n"
        "- Lihat kolom NYATA sebuah database (anti-mengarang kolom): "
        "{\"action\":\"columns\",\"db\":\"<key>\"}.\n"
        "- Ambil data: {\"action\":\"query\",\"db\":\"<key>\",\"sql\":\"SELECT ...\"}.\n"
        "  * Hanya SELECT/WITH (read-only). Satu statement, tanpa ';'. Sertakan LIMIT wajar.\n"
        + cs_line
        + rag_line +
        "- Bila sudah cukup untuk menjawab: {\"action\":\"final\",\"answer\":\"...\"}.\n"
        "  * 'answer' Bahasa Indonesia, ringkas, jelas, boleh Markdown, sebutkan angka penting.\n"
        "  * Jangan mengarang data di luar hasil query.\n\n"
        "Database tersedia (key | label | tabel):\n" + _catalog_text() +
        "\n\nAturan penting:\n"
        "- Gunakan HANYA key database pada daftar di atas. Database 'users' TIDAK tersedia.\n"
        "- Untuk pertanyaan lintas-topik/lintas-DB, query tiap DB TERPISAH lalu gabungkan "
        "di penalaran (tidak ada JOIN antar-DB).\n"
        "- Maksimal " + str(MAX_QUERY_STEPS) + " langkah query; setelah itu WAJIB 'final'.\n"
        "- Jika data tidak ditemukan, jujur katakan belum tersedia di data internal."
        + _query_hints()
    )


def _parse_action(raw):
    """Ambil objek aksi JSON dari balasan model; toleran terhadap markdown."""
    raw = (raw or "").strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.S | re.I)
    cand = m.group(1) if m else None
    if not cand:
        m = re.search(r"\{.*\}", raw, re.S)
        cand = m.group(0) if m else raw
    try:
        obj = json.loads(cand)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    # fallback: perlakukan seluruh balasan sebagai jawaban final
    return {"action": "final", "answer": raw}


def _audit(question, trace, status):
    """Audit best-effort ke stdout (PII di-mask). Tidak pernah menggagalkan request."""
    try:
        rec = {
            "ts": _now(),
            "status": status,
            "question": pii_mask.mask_text(question or ""),
            "steps": [
                {"type": t.get("type"), "db": t.get("db"), "ok": t.get("ok"),
                 "rows": t.get("rows"), "error": t.get("error")}
                for t in trace
            ],
        }
        print("[agentic-audit] " + json.dumps(rec, ensure_ascii=False), flush=True)
    except Exception:
        pass


def answer_agentic(question, lang=None, max_iters=MAX_ITERS):
    """Jalankan loop agentic read-only. Return dict siap-JSON.

    {ok, mode:'agentic', answer, steps:[...], databases:[...], note?}
    """
    q = (question or "").strip()
    if not q:
        return {"ok": False, "error": "question kosong."}

    system = _system_prompt()
    if kctx is not None:
        try:
            system += kctx.system_suffix(q)
        except Exception:
            pass

    messages = [{"role": "user", "content": pii_mask.mask_text("Pertanyaan: " + q)}]
    trace = []
    used_dbs = []
    query_steps = 0
    rag_steps = 0

    turns = max(1, int(max_iters))
    for _ in range(turns):
        raw = llm_client.chat(
            messages, system=pii_mask.mask_text(system),
            max_new_tokens=700, temperature=0.0)
        messages.append({"role": "assistant", "content": raw})
        act = _parse_action(raw)
        action = (act.get("action") or "").strip().lower()

        if action == "final":
            answer = act.get("answer") or ""
            _audit(q, trace, "final")
            return {"ok": True, "mode": "agentic", "answer": answer,
                    "steps": trace, "databases": sorted(set(used_dbs))}

        if action == "schema":
            key = (act.get("db") or "").strip()
            sc = registry.get_schema(key)
            trace.append({"type": "schema", "db": key, "ok": bool(sc.get("ok"))})
            obs = "OBSERVASI (schema " + key + "):\n" + json.dumps(sc, ensure_ascii=False)
            if sc.get("ok") and data_dict is not None:
                try:
                    g = data_dict.grounding_text(key)
                except Exception:
                    g = ""
                if g:
                    obs += "\n" + g
            messages.append({"role": "user", "content": _clip(obs, MAX_SCHEMA_CHARS)})
            continue

        if action == "columns":
            key = (act.get("db") or "").strip()
            cols = registry.get_columns(key)
            trace.append({"type": "columns", "db": key, "ok": bool(cols.get("ok"))})
            messages.append({"role": "user", "content": _clip(
                "OBSERVASI (columns " + key + "):\n" +
                json.dumps(cols, ensure_ascii=False))})
            continue

        if action == "content_search":
            key = (act.get("db") or "").strip()
            pattern = (act.get("pattern") or act.get("regex") or "").strip()
            if content_search is None:
                messages.append({"role": "user", "content":
                    "Aksi 'content_search' tidak tersedia. Gunakan REGEXP pada kolom "
                    "transkrip lewat 'query', atau 'columns'/'final'."})
                continue
            if not pattern:
                messages.append({"role": "user", "content":
                    "content_search butuh 'pattern' (regex). Balas JSON aksi lagi."})
                continue
            if query_steps >= MAX_QUERY_STEPS:
                messages.append({"role": "user", "content":
                    "Batas langkah tercapai. Balas sekarang dengan "
                    "{\"action\":\"final\",\"answer\":\"...\"}."})
                continue
            query_steps += 1
            try:
                cres = content_search.search(
                    key or "avaya", pattern,
                    scope=(act.get("scope") or "chat"),
                    exclude_bot_only=bool(act.get("exclude_bot_only", True)),
                    prefilter=(act.get("prefilter") or act.get("contains") or ""),
                    limit=int(act.get("limit") or 500))
            except Exception as e:
                cres = {"ok": False, "error": str(e)}
            ok = bool(cres.get("ok"))
            if key:
                used_dbs.append(key)
            trace.append({
                "type": "content_search", "db": key, "ok": ok,
                "error": (None if ok else cres.get("error")),
                "rows": (cres.get("total") if ok else 0)})
            if ok:
                obs = json.dumps({
                    "db": key, "columns": cres.get("columns"),
                    "rows": (cres.get("rows") or [])[:MAX_ROWS_TO_LLM],
                    "total": cres.get("total"), "scanned": cres.get("scanned"),
                    "truncated": cres.get("truncated")}, ensure_ascii=False)
            else:
                obs = json.dumps({"db": key, "error": cres.get("error")},
                                 ensure_ascii=False)
            messages.append({"role": "user",
                             "content": _clip("OBSERVASI (content_search):\n" + obs)})
            continue

        if action == "query":
            key = (act.get("db") or "").strip()
            sql = (act.get("sql") or "").strip()
            if query_steps >= MAX_QUERY_STEPS:
                messages.append({"role": "user", "content":
                    "Batas langkah query tercapai. Balas sekarang dengan "
                    "{\"action\":\"final\",\"answer\":\"...\"}."})
                continue
            query_steps += 1
            res = registry.run_select(key, sql, max_rows=MAX_ROWS)
            ok = bool(res.get("ok"))
            if key:
                used_dbs.append(key)
            trace.append({
                "type": "query", "db": key, "sql": res.get("sql", sql),
                "ok": ok, "error": (None if ok else res.get("error")),
                "rows": (len(res.get("rows", [])) if ok else 0)})
            if ok:
                obs = json.dumps({
                    "db": key, "columns": res.get("columns"),
                    "rows": res.get("rows", [])[:MAX_ROWS_TO_LLM]},
                    ensure_ascii=False)
            else:
                err = res.get("error") or ""
                obs_obj = {"db": key, "error": err, "sql": res.get("sql", sql)}
                # Auto-koreksi: bila kolom tak ada, sertakan kolom NYATA + petunjuk isi.
                if "no such column" in err.lower() and key:
                    try:
                        _cols = registry.get_columns(key)
                        if _cols.get("ok"):
                            obs_obj["kolom_tersedia"] = _cols.get("columns")
                            obs_obj["petunjuk"] = (
                                "Kolom conversation_text/isi_percakapan TIDAK ADA. "
                                "Isi percakapan CHAT ada di 'transkrip_json', TELEPON di "
                                "'stt_text'. Untuk mencari pola pada isi, pakai aksi "
                                "'content_search' atau REGEXP pada kolom transkrip.")
                    except Exception:
                        pass
                obs = json.dumps(obs_obj, ensure_ascii=False)
            messages.append({"role": "user",
                             "content": _clip("OBSERVASI (query):\n" + obs)})
            continue

        if action == "rag_search":
            if rag_kb is None:
                messages.append({"role": "user", "content":
                    "Aksi 'rag_search' tidak tersedia. Gunakan 'schema'/'query'/'final'."})
                continue
            if rag_steps >= MAX_RAG_STEPS:
                messages.append({"role": "user", "content":
                    "Batas langkah rag_search tercapai. Lanjutkan dengan 'query' atau 'final'."})
                continue
            rag_steps += 1
            rq = (act.get("query") or act.get("q") or q).strip()
            rsrc = act.get("sources")
            if not isinstance(rsrc, list):
                rsrc = None
            try:
                rr = rag_kb.retrieve_context(rq, sources=rsrc, max_chars=MAX_RAG_CHARS)
            except Exception as e:
                rr = {"ok": False, "error": str(e), "context": "", "sources": [], "used": []}
            ok = bool(rr.get("ok")) and bool((rr.get("context") or "").strip())
            trace.append({
                "type": "rag_search",
                "db": (",".join(rr.get("used") or []) or None),
                "ok": ok, "error": (None if rr.get("ok") else rr.get("error")),
                "rows": len(rr.get("sources") or [])})
            if ok:
                obs = json.dumps({
                    "query": rq, "used": rr.get("used"),
                    "context": rr.get("context"),
                    "sources": rr.get("sources")}, ensure_ascii=False)
            else:
                obs = json.dumps({
                    "query": rq,
                    "error": (rr.get("error") or "tidak ada hasil relevan"),
                    "used": rr.get("used")}, ensure_ascii=False)
            messages.append({"role": "user",
                             "content": _clip("OBSERVASI (rag_search):\n" + obs)})
            continue

        # aksi tak dikenal / balasan tanpa action -> jika ada 'answer', pakai;
        # kalau tidak, minta model menutup dengan JSON yang benar.
        if act.get("answer"):
            _audit(q, trace, "final_fallback")
            return {"ok": True, "mode": "agentic", "answer": act.get("answer"),
                    "steps": trace, "databases": sorted(set(used_dbs))}
        messages.append({"role": "user", "content":
            "Aksi tidak dikenal. Balas JSON valid: "
            "{\"action\":\"query\",\"db\":\"...\",\"sql\":\"SELECT ...\"} "
            "atau {\"action\":\"final\",\"answer\":\"...\"}."})

    # Batas giliran tercapai -> minta ringkasan final sekali lagi (best-effort).
    try:
        summary = llm_client.chat(
            messages + [{"role": "user", "content":
                         "Batas langkah tercapai. Berdasarkan observasi di atas, "
                         "jawab sekarang dalam Bahasa Indonesia (ringkas, sebutkan "
                         "angka penting). Jangan mengarang di luar hasil."}],
            system=pii_mask.mask_text(system),
            max_new_tokens=700, temperature=0.2)
    except Exception as e:
        summary = "Maaf, gagal menyusun jawaban akhir: " + str(e)
    _audit(q, trace, "max_turns")
    return {"ok": True, "mode": "agentic", "answer": summary,
            "steps": trace, "databases": sorted(set(used_dbs)),
            "note": "batas langkah tercapai"}


if __name__ == "__main__":
    # Smoke test offline-safe: hanya menguji katalog & parser (tanpa LLM/DB).
    cat = _catalog_text()
    assert "users" not in cat, "users tidak boleh muncul di katalog agentic"
    assert "analytics" in cat, "analytics harus muncul di katalog"
    a1 = _parse_action('{"action":"query","db":"analytics","sql":"SELECT 1"}')
    assert a1.get("action") == "query" and a1.get("db") == "analytics", a1
    a2 = _parse_action('```json\n{"action":"final","answer":"hai"}\n```')
    assert a2.get("action") == "final", a2
    a3 = _parse_action("jawaban biasa tanpa json")
    assert a3.get("action") == "final", a3
    a4 = _parse_action('{"action":"rag_search","query":"efin","sources":["peraturan"]}')
    assert a4.get("action") == "rag_search" and a4.get("query") == "efin", a4
    a5 = _parse_action('{"action":"content_search","db":"avaya","pattern":"x"}')
    assert a5.get("action") == "content_search" and a5.get("db") == "avaya", a5
    assert MAX_QUERY_STEPS > 0 and MAX_ITERS >= MAX_QUERY_STEPS, (MAX_QUERY_STEPS, MAX_ITERS)
    print("AGENTIC_SMOKE_OK steps=", MAX_QUERY_STEPS, "iters=", MAX_ITERS)
