# -*- coding: utf-8 -*-
"""ask_precise_routes.py — Mode PRESISI single-DB (Fase 6).

Endpoint /api/ask-precise: text-to-SQL read-only pada SATU database terdaftar
(db.registry) sesuai halaman (AWE/Sosmed/Peraturan/SOP/Kamus). Ini membuat mode
DEFAULT (tak centang) di halaman-halaman itu hanya mencari di DB terkait, bukan
menelusuri semua DB seperti agentic.

v2 (grounding + mini-loop): prompt kini disuntik KAMUS DATA (deskripsi kolom,
nilai kategorikal nyata, contoh) dari knowledge/data_dictionary.py, dan penulisan
SQL dijalankan dalam MINI-LOOP: bila query gagal (mis. 'no such column') atau
mengembalikan 0 baris, model diberi umpan balik (kolom nyata / saran longgarkan)
lalu mencoba lagi hingga PRECISE_MAX_RETRIES kali. Tetap READ-ONLY & single-DB.

SIFAT: ADITIF & NON-BREAKING — modul & endpoint baru; /api/ask, /api/ask-data,
dan /api/ask-agentic TIDAK diubah. SELECT diarahkan lewat db.registry.run_select
(read-only, users dikecualikan, LIMIT dipaksa).

Daftarkan dengan:
    import knowledge.ask_precise_routes as ap; ap.register(app)
"""
import os
import re
import json
import datetime as _dt

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

import db.registry as registry
import common.llm_client as llm_client
import common.pii_mask as pii_mask

try:
    import knowledge.data_dictionary as data_dict
except Exception:  # pragma: no cover - grounding opsional
    data_dict = None

try:
    from knowledge import ctx as kctx
except Exception:  # pragma: no cover - kctx opsional
    kctx = None

try:
    from knowledge.routes import ASK_AGENTIC_SCOPES
except Exception:  # pragma: no cover - fallback bila import gagal
    ASK_AGENTIC_SCOPES = {}


def _env_int(name, default):
    try:
        v = int(os.environ.get(name, "") or default)
        return v if v >= 0 else default
    except Exception:
        return default


# Berapa kali model boleh MEMPERBAIKI SQL setelah percobaan pertama.
PRECISE_MAX_RETRIES = _env_int("PRECISE_MAX_RETRIES", 2)


# Halaman -> SATU database (registry key) untuk mode presisi single-DB.
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


def _today_jkt():
    """Tanggal hari ini zona Asia/Jakarta (fallback UTC+7)."""
    try:
        from zoneinfo import ZoneInfo
        return _dt.datetime.now(ZoneInfo("Asia/Jakarta")).date()
    except Exception:
        tz = _dt.timezone(_dt.timedelta(hours=7))
        return _dt.datetime.now(tz).date()


def _query_hints():
    """Petunjuk umum text-to-SQL: sadar tanggal + fuzzy identitas + pemisahan
    tegas antara IDENTITAS vs ISI percakapan (anti-halusinasi kolom).
    """
    today = _today_jkt().isoformat()
    return (
        "\n\nKonteks waktu: hari ini = " + today + " (zona Asia/Jakarta). "
        "Pertanyaan relatif seperti 'hari ini', 'kemarin', 'minggu ini', "
        "'bulan ini', atau '30 hari terakhir' dihitung dari tanggal tersebut. "
        "Bila kolom tanggal bertipe TEXT, pakai substr(kolom,1,10) untuk filter "
        "per hari (mis. substr(tanggal,1,10) >= '" + today + "').\n"
        "Pencarian IDENTITAS/teks kolom (nama pelanggan/customer, SID, nomor "
        "telepon/ANI, nama agen, NIK, topik, intent): gunakan pencocokan SEBAGIAN "
        "& tidak peka huruf besar-kecil, mis. WHERE lower(customer) LIKE "
        "lower('%kata%'). JANGAN memakai kecocokan sama-persis (=) untuk nama/teks "
        "kecuali pengguna memberi nilai yang jelas eksak (mis. SID/ID lengkap).\n"
        "PENTING — IDENTITAS vs ISI PERCAKAPAN: kolom customer/ani/nik/agent_name "
        "adalah IDENTITAS, BUKAN isi percakapan. ISI/teks percakapan yang "
        "sebenarnya (yang diketik/diucapkan customer & agent, termasuk alamat "
        "email yang diketik) ada di kolom TRANSKRIP: transkrip_json untuk CHAT "
        "(awe_conversations) dan stt_text/transkrip_json untuk TELEPON "
        "(awe_phone_interactions). TIDAK ADA kolom bernama conversation_text, "
        "isi_percakapan, atau transcript — JANGAN mengarang nama kolom; bila ragu "
        "pakai hanya kolom yang tercantum di skema/kamus data.\n"
        "Bila pengguna meminta pencarian pada ISI percakapan, cocokkan ke kolom "
        "transkrip, JANGAN ke customer. Fungsi REGEXP TERSEDIA (case-insensitive) "
        "untuk pola pada isi; pakai di WHERE tetapi JANGAN mem-SELECT kolom "
        "transkrip besar itu — cukup SELECT sid, customer AS nama, nik. Contoh "
        "email @gmail.com dengan LEBIH DARI SATU titik pada bagian sebelum @ "
        "(mis. sam.sul.h@gmail.com, s.a.m.s.u.l.h@gmail.com; sedangkan "
        "wp1@gmail.com atau nico.reno@gmail.com yang 0/1 titik TIDAK dicari): "
        "WHERE transkrip_json REGEXP "
        r"'[A-Za-z0-9_%+-]+(?:\.[A-Za-z0-9_%+-]+){2,}@gmail\.com'"
        ".\n"
        "'bot-only' (murni bot) = agent_name kosong; untuk MENGECUALIKAN bot-only "
        "tambahkan AND agent_name IS NOT NULL AND agent_name<>''. Bila sebuah "
        "query mengembalikan 0 baris, longgarkan (LIKE lebih longgar / lepas filter "
        "tanggal / periksa nilai kategorikal) sebelum menyimpulkan data tidak ada."
    )


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


def _grounding(db_key):
    if data_dict is None:
        return ""
    try:
        return data_dict.grounding_text(db_key) or ""
    except Exception:
        return ""


def answer_precise(question, page, db_key):
    """Text-to-SQL read-only pada SATU database terdaftar (registry), dengan
    grounding kamus data + mini-loop auto-retry untuk memperbaiki SQL."""
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
    sys1 += _query_hints()
    sys1 += _grounding(db_key)
    _scope = ASK_AGENTIC_SCOPES.get((page or "").strip().lower())
    if _scope:
        sys1 += "\n\n" + _scope

    convo = [{"role": "user", "content": pii_mask.mask_text(question)}]
    res = None
    last_sql = ""
    attempts = 0
    for attempt in range(PRECISE_MAX_RETRIES + 1):
        attempts += 1
        raw = llm_client.chat(convo, system=sys1, max_new_tokens=400, temperature=0.0)
        convo.append({"role": "assistant", "content": raw})
        sql = _extract_sql(raw)
        last_sql = sql
        res = registry.run_select(db_key, sql, max_rows=200)
        if res.get("ok") and res.get("rows"):
            break
        if attempt >= PRECISE_MAX_RETRIES:
            break
        # Susun umpan balik untuk percobaan berikutnya.
        if not res.get("ok"):
            err = res.get("error") or ""
            fb = "Query GAGAL: " + str(err)
            if "no such column" in err.lower():
                try:
                    cinfo = registry.get_columns(db_key)
                    if cinfo.get("ok"):
                        fb += "\nKolom NYATA per tabel: " + json.dumps(
                            cinfo.get("columns"), ensure_ascii=False)
                except Exception:
                    pass
                fb += ("\nIngat: isi percakapan ada di transkrip_json (chat) / stt_text "
                       "(telepon); tidak ada conversation_text. ")
            fb += "Perbaiki dan balas HANYA JSON {\"sql\":\"...\"}."
            convo.append({"role": "user", "content": fb})
        else:
            convo.append({"role": "user", "content":
                "Query valid tetapi 0 baris. Coba longgarkan filter (LIKE lebih "
                "longgar, lepaskan filter tanggal, atau periksa nilai kategorikal "
                "pada kamus data). Balas HANYA JSON {\"sql\":\"...\"}."})

    if not res or not res.get("ok"):
        return {"ok": False, "mode": "data",
                "error": (res or {}).get("error", "Query gagal."),
                "sql": (res or {}).get("sql", last_sql), "db": db_key,
                "attempts": attempts}

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
    return {"ok": True, "mode": "data", "answer": answer, "sql": res.get("sql", last_sql),
            "columns": res.get("columns"), "rows": res.get("rows", [])[:50],
            "db": db_key, "attempts": attempts}


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
