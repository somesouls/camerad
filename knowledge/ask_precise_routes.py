# -*- coding: utf-8 -*-
"""ask_precise_routes.py — Mode PRESISI single-DB (Fase 6).

Endpoint /api/ask-precise: text-to-SQL read-only pada SATU database terdaftar
(db.registry) sesuai halaman (AWE/Sosmed/Peraturan/SOP/Kamus). Ini membuat mode
DEFAULT (tak centang) di halaman-halaman itu hanya mencari di DB terkait, bukan
menelusuri semua DB seperti agentic.

SIFAT: ADITIF & NON-BREAKING — modul & endpoint baru; /api/ask, /api/ask-data,
dan /api/ask-agentic TIDAK diubah. SELECT diarahkan lewat db.registry.run_select
(read-only, users dikecualikan, LIMIT dipaksa).

Daftarkan dengan:
    import knowledge.ask_precise_routes as ap; ap.register(app)
"""
import re
import json

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

import db.registry as registry
import common.llm_client as llm_client
import common.pii_mask as pii_mask

try:
    from knowledge import ctx as kctx
except Exception:  # pragma: no cover - kctx opsional
    kctx = None

try:
    from knowledge.routes import ASK_AGENTIC_SCOPES
except Exception:  # pragma: no cover - fallback bila import gagal
    ASK_AGENTIC_SCOPES = {}


# Halaman -> SATU database (registry key) untuk mode presisi single-DB.
# Semua halaman ini memakai tepat satu database sumber.
PRECISION_DB = {
    "awe_dasbor": "avaya",
    "awe_coverage": "avaya",
    "awe_taksonomi": "avaya",
    "awe_sentimen": "avaya",
    "awe_percakapan": "avaya",
    "awe_pengguna": "avaya",
    "awe_penilaian": "avaya",
    "awe_telepon_dash": "avaya",
    "awe_telepon_cov": "avaya",
    "awe_telepon_tax": "avaya",
    "awe_telepon_sen": "avaya",
    "awe_telepon_detail": "avaya",
    "awe_telepon_users": "avaya",
    "sosmed_qna": "sosmed",
    "sosmed_sla": "sosmed",
    "sosmed_deflection": "sosmed",
    "peraturan": "peraturan",
    "sop": "sop",
    "kamus": "kamus",
}


def _extract_sql(raw):
    raw = (raw or "").strip()
    m = re.search(r"\{.*\}", raw, re.S)
    if m:
        try:
            j = json.loads(m.group(0))
            if isinstance(j, dict) and j.get("sql"):
                return str(j["sql"]).strip()
        except Exception:
            pass
    m = re.search(r"```(?:sql)?\s*(.+?)```", raw, re.S | re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"(select\b.+)", raw, re.S | re.I)
    if m:
        return m.group(1).strip()
    return raw


def _ctx_suffix(question):
    if kctx is None:
        return ""
    try:
        return kctx.system_suffix(question) or ""
    except Exception:
        return ""


def answer_precise(question, page, db_key):
    """Text-to-SQL read-only pada SATU database terdaftar (registry)."""
    sc = registry.get_schema(db_key)
    if not sc.get("ok"):
        return {"ok": False, "mode": "data",
                "error": sc.get("error", "Database tidak dikenal."), "db": db_key}
    tables = ", ".join(sc.get("tables") or []) or "-"
    schema_text = sc.get("schema") or ""
    sys1 = (
        'Kamu ahli SQLite. Ubah pertanyaan pengguna menjadi SATU query SELECT '
        'read-only untuk menjawabnya. Balas HANYA JSON {"sql":"..."} tanpa '
        'penjelasan, tanpa markdown.\n'
        'Database: ' + str(db_key) + ' (' + str(sc.get("label", db_key)) + ').\n'
        'Tabel tersedia: ' + tables + '.\n'
        'Skema:\n' + schema_text + '\n'
        'Aturan: HANYA SELECT/WITH, satu statement tanpa ";", selalu sertakan '
        'LIMIT wajar, dan jangan mengarang tabel/kolom di luar skema.'
    )
    _scope = ASK_AGENTIC_SCOPES.get((page or "").strip().lower())
    if _scope:
        sys1 += "\n\n" + _scope
    raw = llm_client.chat([{"role": "user", "content": pii_mask.mask_text(question)}],
                          system=sys1, max_new_tokens=400, temperature=0.0)
    sql = _extract_sql(raw)
    res = registry.run_select(db_key, sql, max_rows=200)
    if not res.get("ok"):
        return {"ok": False, "mode": "data",
                "error": res.get("error", "Query gagal."),
                "sql": res.get("sql", sql), "db": db_key}
    preview = json.dumps({"columns": res.get("columns"),
                          "rows": res.get("rows", [])[:50]}, ensure_ascii=False)
    sys2 = (
        'Jawab pertanyaan pengguna dalam Bahasa Indonesia secara ringkas, jelas, '
        'dan enak dibaca berdasarkan HASIL query di bawah. Sebutkan angka penting. '
        'Jangan mengarang data di luar hasil.'
    ) + _ctx_suffix(question)
    answer = llm_client.chat(
        [{"role": "user", "content": pii_mask.mask_text("Pertanyaan: " + question +
          "\n\nHasil query (JSON):\n" + preview)}],
        system=pii_mask.mask_text(sys2), max_new_tokens=700, temperature=0.2)
    return {"ok": True, "mode": "data", "answer": answer, "sql": res.get("sql", sql),
            "columns": res.get("columns"), "rows": res.get("rows", [])[:50],
            "db": db_key}


async def api_ask_precise(request: Request):
    """Body: {question, page}. Presisi single-DB sesuai halaman."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    question = (body.get("question") or "").strip()
    page = (body.get("page") or "").strip().lower()
    if not question:
        return JSONResponse({"ok": False, "error": "question kosong."})
    db_key = PRECISION_DB.get(page)
    if not db_key:
        return JSONResponse({"ok": False,
                             "error": "Halaman ini belum punya database presisi terdaftar."})
    try:
        return JSONResponse(await run_in_threadpool(answer_precise, question, page, db_key))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


def register(app):
    app.add_api_route("/api/ask-precise", api_ask_precise, methods=["POST"])
