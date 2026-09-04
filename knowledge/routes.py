# -*- coding: utf-8 -*-
"""knowledge_routes.py — Tanya AI kontekstual: text-to-SQL (data) & jawaban
berpagar dari pustaka internal (Epik B). Migrasi langkah 4 dari web_app.py.

Daftarkan dengan:
    import knowledge_routes; knowledge_routes.register(app)
"""
import re
import json

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

import db.analytics_db as adb
from knowledge import glossary_db as gdb
from knowledge import disambig_db as ddb
from knowledge import intentmap_db as imdb
from knowledge import ctx as kctx
from knowledge import agentic as agentic
import common.llm_client as llm_client
import common.pii_mask as pii_mask


# =============================================================
# TAHAP 3 — Scope presisi per-halaman (ADITIF & NON-BREAKING).
# Registry opsional; halaman yang TIDAK terdaftar berperilaku SAMA PERSIS
# seperti sebelumnya (tanpa tambahan scope).
#   - ASK_DATA_SCOPES      : panduan tambahan text-to-SQL utk halaman DATA.
#   - ASK_KNOWLEDGE_SCOPES : penajaman guardrail utk halaman PUSTAKA.
#   - ASK_AGENTIC_SCOPES   : konteks halaman utk jalur AGENTIC (mis. Sosmed).
# Tidak menyentuh analytics_db, pipeline ingest, atau halaman lain.
# =============================================================
ASK_DATA_SCOPES = {
    "tools": (
        "KONTEKS HALAMAN: 'Analisis Dialogflow' \u2014 analisis mutu & cakupan "
        "bot Dialogflow dari tabel `interactions`. Patuhi konvensi berikut:\n"
        "- Intent bisnis/'bersih' = is_system=0 AND is_fallback=0 AND "
        "substr(intent_name,1,7)<>'System_' AND substr(intent_name,1,5)<>'Umum_'.\n"
        "- Pertanyaan tak dikenali (kandidat intent baru) = is_fallback=1.\n"
        "- Intent sistem (welcome/hubungi agent) = is_system=1 (awalan 'System_').\n"
        "- Fallback rate = SUM(is_fallback)*100.0/COUNT(*).\n"
        "- Jumlah percakapan = COUNT(DISTINCT session_id); interaksi = COUNT(*).\n"
        "- Filter tanggal SELALU memakai kolom `day` (YYYY-MM-DD, Asia/Jakarta).\n"
        "- Kolom `ts` UTC ISO; untuk analisis jam sibuk konversi ke WIB dengan "
        "datetime(replace(substr(ts,1,19),'T',' '),'+7 hours').\n"
        "- 'Top intent' = urut COUNT(*) DESC memakai filter intent bersih di atas.\n"
        "Hanya query tabel `interactions`; jangan mengarang tabel/kolom lain."
    ),
}

ASK_KNOWLEDGE_SCOPES = {
    "intentmap": (
        "KONTEKS HALAMAN: 'Peta Intent' berisi (a) keputusan/kebijakan analis "
        "sebagai KUNCI JAWABAN tiap intent, dan (b) Katalog Intent (deskripsi "
        "maksud & cakupan; sebagian masih DRAF AI yang belum diverifikasi). "
        "Utamakan keputusan analis yang sudah terverifikasi; bila informasi "
        "hanya tersedia dari draf katalog, sampaikan seadanya dan tandai sebagai "
        "draf. Untuk pertanyaan 'intent untuk X', sebutkan nama intent yang tepat "
        "beserta maksud/cakupannya sesuai peta/katalog. Jangan mengarang nama "
        "intent yang tidak ada dalam konteks."
    ),
}

ASK_AGENTIC_SCOPES = {
    "sosmed_qna": (
        "KONTEKS HALAMAN: 'Q&A Sosmed' \u2014 kumpulan pertanyaan warga & utas "
        "dari media sosial (X/IG/TikTok). Untuk menjawab, UTAMAKAN database "
        "`sosmed` (tabel `sosmed_items`; pertanyaan warga = "
        "item_type='pertanyaan', teks pada kolom `text`, waktu pada kolom `ts`). "
        "Untuk 'pertanyaan tersering/terbaru', agregasi/urutkan `sosmed_items`. "
        "Jangan mencampur dengan data Dialogflow (`analytics`/`interactions`) "
        "kecuali memang diminta."
    ),
    "sosmed_sla": (
        "KONTEKS HALAMAN: 'SLA & Analitik Sosmed' \u2014 cakupan/keterjawaban & "
        "analitik interaksi media sosial. UTAMAKAN database `sosmed` (tabel "
        "`sosmed_items`, `sosmed_batches`). Hitung volume & keterjawaban dari "
        "`sosmed_items` (pertanyaan = item_type='pertanyaan'); pakai kolom `ts` "
        "untuk tren waktu. Jangan mencampur dengan data Dialogflow "
        "(`analytics`/`interactions`)."
    ),
    "sosmed_deflection": (
        "KONTEKS HALAMAN: 'Coverage & Deflection Sosmed' \u2014 klaster pertanyaan "
        "warga yang sedang tren dan potensi gap pengetahuan bot. UTAMAKAN "
        "database `sosmed` (tabel `sosmed_items`, pertanyaan = "
        "item_type='pertanyaan') untuk menemukan pertanyaan berulang/tren. "
        "Jangan mengarang klaster di luar data yang ada."
    ),
    "peraturan": (
        "KONTEKS HALAMAN: 'Basis Data Peraturan' \u2014 basis data regulasi "
        "pajak per unit (pasal/ayat/lampiran). Untuk RANGKUMAN/REKAP/CROSS-CHECK "
        "berbasis SQL, UTAMAKAN database `peraturan` (tabel `peraturan_unit`; "
        "kolom a.l. jenis_peraturan, nomor, tahun, judul, pasal, ayat, isi, "
        "status['berlaku'/'dicabut'/'diubah'], topik; relasi antar-peraturan di "
        "`peraturan_relasi`). Cocok untuk: rekap COUNT/GROUP BY "
        "jenis_peraturan/tahun/status, cek pencabutan/perubahan via "
        "`peraturan_relasi`, dan cross-check silang-tabel (mis. peraturan yang "
        "belum tercakup di SOP -> bandingkan dengan database `sop`). Pencarian "
        "isi/judul pakai LIKE (mis. judul LIKE '%PPN%'). CATATAN: pencarian "
        "makna/kualitas jawaban regulasi ditangani mesin RAG, BUKAN jalur ini; "
        "di sini andalkan filter SQL (LIKE/agregasi) dan akui bila cakupan teks "
        "terbatas. Jangan SELECT tabel `peraturan_vec`/`peraturan_fts`. Jangan "
        "mengarang nomor/pasal."
    ),
    "sop": (
        "KONTEKS HALAMAN: 'SOP & Proses Bisnis'. Untuk RANGKUMAN/REKAP/CROSS-"
        "CHECK berbasis SQL, UTAMAKAN database `sop` (tabel `sop_unit`; kolom "
        "a.l. dokumen_id, judul, kategori['SOP'/'Proses Bisnis'/'Panduan'/"
        "'Lainnya'], bagian, isi, status['aktif']). Cocok untuk: rekap "
        "COUNT/GROUP BY kategori/dokumen_id, inventarisasi SOP aktif, dan "
        "cross-check silang-tabel dengan database `peraturan` (mis. peraturan "
        "yang belum punya SOP terkait). Pencarian pakai LIKE pada judul/isi. "
        "CATATAN: pencarian makna/kualitas ditangani mesin RAG, BUKAN jalur ini. "
        "Jangan SELECT tabel `sop_vec`/`sop_fts`. Jangan mengarang isi SOP di "
        "luar data."
    ),
    "kamus": (
        "KONTEKS HALAMAN: 'Kamus & Rewriting' \u2014 kamus sinonim/istilah pajak "
        "untuk normalisasi query. UTAMAKAN database `kamus` (tabel "
        "`kamus_sinonim`; kolom istilah, sinonim [JSON array], kategori, aktif). "
        "Cocok untuk: rekap COUNT/GROUP BY kategori, mencari padanan/sinonim "
        "istilah (LIKE pada kolom istilah; sinonim disimpan sebagai JSON array), "
        "dan cek istilah aktif/nonaktif. CATATAN: gunakan untuk lookup/rekap, "
        "bukan pencarian makna dokumen. Jangan mengarang istilah di luar data."
    ),
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


def answer_data_question(question, page=None):
    """AI tanya-jawab data: text-to-SQL read-only + rangkum jawaban natural.

    `page` opsional (Tahap 3): bila terdaftar di ASK_DATA_SCOPES, panduan scope
    khusus halaman ditambahkan ke prompt SQL. Bila None / tak terdaftar,
    perilakunya identik dengan versi sebelumnya.
    """
    conn = adb.init_db(adb.connect())
    try:
        sys1 = (
            "Kamu ahli SQLite. Ubah pertanyaan pengguna menjadi SATU query "
            "SELECT untuk menjawabnya. Balas HANYA JSON {\"sql\":\"...\"} tanpa "
            "penjelasan, tanpa markdown.\n" + adb.SCHEMA_FOR_LLM +
            "\nHari ini (Asia/Jakarta): " + adb._jkt_today() +
            ". Untuk 'minggu lalu' gunakan rentang tanggal pada kolom day. "
            "Selalu tambahkan LIMIT yang wajar."
        )
        _scope = ASK_DATA_SCOPES.get((page or "").strip().lower())
        if _scope:
            sys1 += "\n\n" + _scope
        raw = llm_client.chat([{"role": "user", "content": pii_mask.mask_text(question)}],
                              system=sys1, max_new_tokens=400, temperature=0.0)
        sql = _extract_sql(raw)
        res = adb.run_select(conn, sql)
        if not res.get("ok"):
            return {"ok": False, "error": res.get("error", "Query gagal."),
                    "sql": res.get("sql", sql)}
        preview = json.dumps({"columns": res["columns"], "rows": res["rows"][:50]},
                             ensure_ascii=False)
        sys2 = (
            "Jawab pertanyaan pengguna dalam Bahasa Indonesia secara ringkas, "
            "jelas, dan enak dibaca berdasarkan HASIL query di bawah. Sebutkan "
            "angka penting. Jangan mengarang data di luar hasil."
        )
        sys2 += kctx.system_suffix(question)
        answer = llm_client.chat(
            [{"role": "user", "content": pii_mask.mask_text("Pertanyaan: " + question +
              "\n\nHasil query (JSON):\n" + preview)}],
            system=pii_mask.mask_text(sys2), max_new_tokens=700, temperature=0.2)
        return {"ok": True, "answer": answer, "sql": res.get("sql", sql),
                "columns": res["columns"], "rows": res["rows"][:50]}
    finally:
        conn.close()


async def api_ask_data(request: Request):
    """AI menjawab pertanyaan tentang data (text-to-SQL, read-only)."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    question = (body.get("question") if isinstance(body, dict) else "") or ""
    question = question.strip()
    if not question:
        return JSONResponse({"ok": False, "error": "question kosong."})
    try:
        return JSONResponse(await run_in_threadpool(answer_data_question, question))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


# =============================================================
# EPIK B: "Tanya AI" kontekstual di SEMUA menu.
# Satu endpoint /api/ask; perilaku ditentukan oleh halaman (page):
#   - Halaman berbasis DATA  -> text-to-SQL read-only atas tabel interactions
#     (dipakai ulang answer_data_question, sama seperti Dashboard).
#   - Halaman berbasis PUSTAKA -> jawaban BERPAGAR: hanya dari database internal
#     pustaka (Glosarium/Disambiguasi/Peta Intent/Katalog); dilarang memakai
#     pengetahuan umum/eksternal, dan wajib mengaku bila info tak tersedia.
# =============================================================
ASK_DATA_PAGES = {"data", "deflection", "lifecycle", "tools", "dashboard"}
ASK_KNOWLEDGE_PAGES = {"glosarium", "disambiguasi", "intentmap"}

ASK_GUARDRAIL = (
    "Kamu asisten internal Camerad Studio untuk tim analis DJP. Jawab HANYA "
    "berdasarkan KONTEKS INTERNAL di bawah, yang berasal dari database internal "
    "tim. DILARANG memakai pengetahuan umum/eksternal, mencari di web, atau "
    "menebak-nebak. Jika informasi yang diminta TIDAK ADA di dalam konteks, "
    "jawab jujur: \"Maaf, informasi itu belum tersedia di data internal untuk "
    "halaman ini.\" Jawab ringkas, jelas, dalam Bahasa Indonesia, boleh Markdown."
)


def build_page_context(page, question, lang=None, max_chars=2600):
    """Penyedia konteks per-halaman: tiap menu pustaka menyuplai potongan
    datanya sendiri dari database internal. Mengembalikan blok teks (bisa
    kosong). Semua kegagalan ditangani diam-diam supaya endpoint tetap jalan."""
    page = (page or "").strip().lower()
    q = (question or "").strip()
    blocks = []
    try:
        if page == "glosarium":
            c = gdb.init_db(gdb.connect())
            try:
                m = gdb.match(c, q, limit=6)
                txt = gdb.build_context_text(m) if m else ""
                if not (txt and txt.strip()):
                    terms = gdb.list_terms(c, q=(q or None), limit=40, lang=lang)
                    names = [t.get("term") for t in terms if t.get("term")]
                    txt = ("Tidak ada entri yang cocok persis. Total %d istilah "
                           "di Glosarium. Contoh istilah tersedia: %s"
                           % (gdb.count(c), ", ".join(names[:40]) or "-"))
                blocks.append("[Glosarium Istilah Pajak]\n" + txt)
            finally:
                c.close()
        elif page == "disambiguasi":
            c = ddb.init_db(ddb.connect())
            try:
                m = ddb.match(c, q, limit=6)
                txt = ddb.build_context_text(m) if m else ""
                if not (txt and txt.strip()):
                    rules = ddb.list_rules(c, q=(q or None), limit=40, lang=lang)
                    names = [r.get("pemicu") for r in rules if r.get("pemicu")]
                    txt = ("Tidak ada aturan yang cocok persis. Total %d aturan "
                           "disambiguasi. Contoh pemicu tersedia: %s"
                           % (ddb.count(c), ", ".join(names[:40]) or "-"))
                blocks.append("[Pustaka Disambiguasi]\n" + txt)
            finally:
                c.close()
        elif page == "intentmap":
            c = imdb.init_db(imdb.connect())
            try:
                m = imdb.match(c, q, limit=5)
                mc = imdb.match_catalog(c, q, limit=5)
                t1 = imdb.build_context_text(m) if m else ""
                t2 = imdb.build_catalog_context_text(mc) if mc else ""
                combined = "\n\n".join([t for t in (t1, t2) if t and t.strip()])
                if not combined:
                    combined = ("Tidak ada entri Peta Intent / Katalog yang cocok "
                                "dengan pertanyaan.")
                blocks.append("[Peta Intent & Katalog]\n" + combined)
            finally:
                c.close()
    except Exception:
        pass
    body = "\n\n".join(b for b in blocks if b and b.strip())
    if max_chars and len(body) > max_chars:
        body = body[:max_chars].rstrip() + "\u2026"
    return body


def answer_knowledge_question(page, question, lang=None):
    """Jawaban berpagar untuk halaman pustaka (Glosarium/Disambiguasi/Intent)."""
    ctx = build_page_context(page, question, lang)
    system = ASK_GUARDRAIL
    if ctx:
        system += ("\n\n=== KONTEKS INTERNAL HALAMAN ===\n" + ctx +
                   "\n=== AKHIR KONTEKS INTERNAL ===")
    # Tahap 3: penajaman scope khusus halaman (aditif; kosong bila tak terdaftar).
    _scope = ASK_KNOWLEDGE_SCOPES.get((page or "").strip().lower())
    if _scope:
        system += "\n\n" + _scope
    # Tambah konteks silang dari pustaka lain yang relevan (glosarium/disambig/
    # peta intent/katalog) agar jawaban tetap konsisten lintas menu.
    system += kctx.system_suffix(question)
    answer = llm_client.chat([{"role": "user", "content": pii_mask.mask_text(question)}],
                             system=pii_mask.mask_text(system), max_new_tokens=800, temperature=0.1)
    return {"ok": True, "mode": "knowledge", "answer": answer,
            "has_context": bool(ctx)}


async def api_ask(request: Request):
    """Tanya AI kontekstual per-halaman. Body: {question, page, lang?}."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    question = (body.get("question") or "").strip()
    page = (body.get("page") or "").strip().lower()
    lang = body.get("lang") or None
    if not question:
        return JSONResponse({"ok": False, "error": "question kosong."})
    try:
        if page in ASK_KNOWLEDGE_PAGES:
            return JSONResponse(await run_in_threadpool(
                answer_knowledge_question, page, question, lang))
        # Default & halaman data: text-to-SQL read-only (sama seperti Dashboard).
        # Tahap 3: teruskan `page` agar scope presisi (mis. 'tools') aktif bila ada.
        res = await run_in_threadpool(answer_data_question, question, page)
        if isinstance(res, dict) and "mode" not in res:
            res["mode"] = "data"
        return JSONResponse(res)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_ask_agentic(request: Request):
    """Tanya AI 'agentic' (Fase 2): loop read-only lintas database via registry.

    Body: {question, lang?, max_iters?, page?}. Non-breaking: endpoint terpisah;
    /api/ask dan /api/ask-data tidak terpengaruh. Tahap 3: bila `page` terdaftar
    di ASK_AGENTIC_SCOPES, konteks halaman ditambahkan sebagai PENGARAH
    penelusuran (mis. menu Sosmed -> database `sosmed`). Engine agentic TIDAK
    diubah; scope hanya menambah konteks pada pertanyaan, halaman lain identik.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    question = (body.get("question") or "").strip()
    lang = body.get("lang") or None
    page = (body.get("page") or "").strip().lower()
    if not question:
        return JSONResponse({"ok": False, "error": "question kosong."})
    try:
        max_iters = int(body.get("max_iters") or agentic.MAX_ITERS)
    except Exception:
        max_iters = agentic.MAX_ITERS
    max_iters = max(1, min(max_iters, agentic.MAX_ITERS))
    _scope = ASK_AGENTIC_SCOPES.get(page)
    q_in = question
    if _scope:
        q_in = ("[KONTEKS HALAMAN untuk mengarahkan penelusuran]\n" + _scope +
                "\n\n[PERTANYAAN PENGGUNA]\n" + question)
    try:
        return JSONResponse(await run_in_threadpool(
            agentic.answer_agentic, q_in, lang, max_iters))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


def register(app):
    app.add_api_route("/api/ask-data", api_ask_data, methods=["POST"])
    app.add_api_route("/api/ask", api_ask, methods=["POST"])
    app.add_api_route("/api/ask-agentic", api_ask_agentic, methods=["POST"])
