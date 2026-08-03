# -*- coding: utf-8 -*-
"""
intent_describe.py -- Job deskripsi AI (draf) untuk Katalog Intent.
Menyimpulkan MAKSUD user & CAKUPAN jawaban tiap intent dari training phrase +
jawaban Dialogflow. AI TIDAK membuat aturan lintas-sistem; ia hanya
mendeskripsikan dan menandai sistem via 'sistem_tersinggung'. Kebenaran domain
tetap milik Pustaka Disambiguasi. Semua hasil DRAF (terverifikasi=0) sampai
disetujui analis. Stdlib-only; LLM via llm_client (bisa diinjeksi lewat chat_fn).
"""
import json
import re
import intentmap_db as imdb

try:
    import llm_client as _llm
except Exception:
    _llm = None

SYSTEM = (
    "Anda analis chatbot pajak DJP. Simpulkan MAKSUD user dan CAKUPAN jawaban "
    "dari satu intent Dialogflow, ringkas & faktual dalam bahasa Indonesia. "
    "JANGAN mengarang aturan lintas-sistem; bila jawaban menyebut sistem "
    "(mis. DJP Online, Coretax, e-Nofa), cukup daftarkan di 'sistem_tersinggung'. "
    "Balas HANYA JSON valid."
)

_USER_TMPL = (
    "Nama intent: {name}\n"
    "Contoh training phrase:\n{phrases}\n\n"
    "Cuplikan jawaban:\n{answer}\n\n"
    "Keluarkan JSON dengan kunci persis:\n"
    '{{"deskripsi_maksud": "<1-2 kalimat: apa yang diinginkan user>", '
    '"deskripsi_cakupan": "<1-2 kalimat: apa yang dijawab bot>", '
    '"sistem_tersinggung": ["<nama sistem bila disebut; boleh kosong>"]}}'
)


def _default_chat(user, system=None):
    if _llm is None:
        raise RuntimeError("llm_client tidak tersedia")
    return _llm.chat([{"role": "user", "content": user}], system=system,
                     max_new_tokens=500, temperature=0.2)


def _extract_json(text):
    if not text:
        return None
    t = str(text).strip()
    m = re.search(r"```(?:json)?\s*(.+?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    start = t.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(t)):
        ch = t[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(t[start:i + 1])
                except Exception:
                    return None
    return None


def _clean_sistem(v):
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, (list, tuple)):
        return []
    out = []
    for x in v:
        s = str(x).strip()
        if s and s.lower() not in ("", "-", "tidak ada", "none"):
            out.append(s)
    return out


def describe_one(intent_name, phrases, answer, chat_fn=None):
    chat = chat_fn or _default_chat
    ph = phrases if isinstance(phrases, (list, tuple)) else [phrases]
    ph_txt = "\n".join("- " + str(p) for p in ph[:12] if str(p).strip()) or "(tidak ada)"
    ans = (answer or "").strip() or "(tidak ada)"
    user = _USER_TMPL.format(name=intent_name, phrases=ph_txt, answer=ans[:800])
    data = _extract_json(chat(user, SYSTEM)) or {}
    return {
        "deskripsi_maksud": str(data.get("deskripsi_maksud", "")).strip(),
        "deskripsi_cakupan": str(data.get("deskripsi_cakupan", "")).strip(),
        "sistem_tersinggung": _clean_sistem(data.get("sistem_tersinggung")),
    }


def run_describe_batch(conn, limit=100, only_called=False, chat_fn=None, progress=None):
    target = imdb.intents_needing_description(conn, limit=limit, only_called=only_called)
    berhasil = gagal = terkunci = 0
    for i, row in enumerate(target):
        try:
            d = describe_one(row.get("intent"), row.get("training_phrase_contoh"),
                             row.get("jawaban_cuplikan"), chat_fn=chat_fn)
            if not d["deskripsi_maksud"] and not d["deskripsi_cakupan"]:
                gagal += 1
                continue
            res = imdb.save_ai_description(conn, row.get("id"), d["deskripsi_maksud"],
                                           d["deskripsi_cakupan"], d["sistem_tersinggung"])
            if isinstance(res, dict) and res.get("locked"):
                terkunci += 1
            elif isinstance(res, dict) and res.get("ok"):
                berhasil += 1
            else:
                gagal += 1
        except Exception:
            gagal += 1
        if progress:
            try:
                progress(i + 1, len(target))
            except Exception:
                pass
    return {"target": len(target), "berhasil": berhasil, "gagal": gagal, "terkunci": terkunci}
