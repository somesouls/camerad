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

Sifat: ADITIF & NON-BREAKING. Modul & endpoint baru; /api/ask dan
/api/ask-data lama tidak diubah perilakunya.
"""
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

# --- Batasan aman (guardrail operasional) --------------------------------
MAX_ITERS = 6            # total giliran model (schema/query/final) per permintaan
MAX_QUERY_STEPS = 6      # batas langkah query aktual
MAX_ROWS = 200           # baris maksimum per query (diteruskan ke run_select)
MAX_ROWS_TO_LLM = 50     # baris yang diumpankan balik ke LLM per observasi
MAX_RESULT_CHARS = 3500  # batas ukuran teks observasi yang diumpan balik


def _now():
    return _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


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
    return (
        "Kamu asisten data internal Camerad untuk tim analis DJP. Kamu menjawab "
        "pertanyaan dengan MENELUSURI beberapa database internal (READ-ONLY).\n\n"
        "Cara kerja (WAJIB):\n"
        "- Balas TEPAT SATU objek JSON per langkah. Tanpa teks lain, tanpa markdown.\n"
        "- Lihat skema kolom dulu bila belum tahu: "
        "{\"action\":\"schema\",\"db\":\"<key>\"}.\n"
        "- Ambil data: {\"action\":\"query\",\"db\":\"<key>\",\"sql\":\"SELECT ...\"}.\n"
        "  * Hanya SELECT/WITH (read-only). Satu statement, tanpa ';'. Sertakan LIMIT wajar.\n"
        "- Bila sudah cukup untuk menjawab: {\"action\":\"final\",\"answer\":\"...\"}.\n"
        "  * 'answer' Bahasa Indonesia, ringkas, jelas, boleh Markdown, sebutkan angka penting.\n"
        "  * Jangan mengarang data di luar hasil query.\n\n"
        "Database tersedia (key | label | tabel):\n" + _catalog_text() +
        "\n\nAturan penting:\n"
        "- Gunakan HANYA key database pada daftar di atas. Database 'users' TIDAK tersedia.\n"
        "- Maksimal " + str(MAX_QUERY_STEPS) + " langkah query; setelah itu WAJIB 'final'.\n"
        "- Jika data tidak ditemukan, jujur katakan belum tersedia di data internal."
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
            messages.append({"role": "user", "content": _clip(
                "OBSERVASI (schema " + key + "):\n" +
                json.dumps(sc, ensure_ascii=False))})
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
                obs = json.dumps({
                    "db": key, "error": res.get("error"),
                    "sql": res.get("sql", sql)}, ensure_ascii=False)
            messages.append({"role": "user",
                             "content": _clip("OBSERVASI (query):\n" + obs)})
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
    print("AGENTIC_SMOKE_OK")
