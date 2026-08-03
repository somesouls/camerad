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


# ==== Drainer draf-AI latar belakang (lazy/bertahap) untuk sisa katalog ====
# Aman untuk ~1.300 intent: berjalan di thread daemon, resumable, non-blocking.
# Termination dijamin: berhenti bila antrean kosong ATAU tak ada kemajuan.
import threading as _threading
import time as _time
import datetime as _dt

_LOCK = _threading.Lock()
_THREAD = None
_STATE = {
    "running": False, "stop": False, "started_at": "", "finished_at": "",
    "done": 0, "ok": 0, "fail": 0, "locked": 0, "remaining": None,
    "target_awal": None, "note": "", "last_error": "", "only_called": False,
}


def _now_iso():
    return _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _count_needing(conn, only_called=False):
    where = "perlu_deskripsi=1 AND sumber_status='aktif' AND sumber_deskripsi=''"
    if only_called:
        where += " AND frekuensi_panggil>0"
    return conn.execute("SELECT COUNT(*) FROM intentmap_catalog WHERE " + where).fetchone()[0]


def describe_progress():
    with _LOCK:
        return dict(_STATE)


def stop_background_drain():
    with _LOCK:
        if not _STATE["running"]:
            return {"ok": True, "running": False, "note": "tidak ada proses berjalan"}
        _STATE["stop"] = True
        return {"ok": True, "running": True, "stopping": True}


def _drain_loop(connect_fn, chat_fn, chunk, sleep_s, max_items, only_called):
    conn = None
    processed = 0
    try:
        conn = connect_fn()
        with _LOCK:
            _STATE["target_awal"] = _count_needing(conn, only_called)
        prev = None
        while True:
            with _LOCK:
                stop = _STATE["stop"]
            if stop:
                with _LOCK:
                    _STATE["note"] = "dihentikan pengguna"
                break
            remaining = _count_needing(conn, only_called)
            with _LOCK:
                _STATE["remaining"] = remaining
            if remaining == 0:
                with _LOCK:
                    _STATE["note"] = "selesai — antrean kosong"
                break
            if prev is not None and remaining >= prev:
                with _LOCK:
                    _STATE["note"] = "berhenti: tak ada kemajuan (LLM gagal/tak tersedia)"
                break
            prev = remaining
            res = run_describe_batch(conn, limit=chunk, only_called=only_called, chat_fn=chat_fn)
            n = res.get("target", 0)
            with _LOCK:
                _STATE["done"] += n
                _STATE["ok"] += res.get("berhasil", 0)
                _STATE["fail"] += res.get("gagal", 0)
                _STATE["locked"] += res.get("terkunci", 0)
            processed += n
            if n == 0:
                with _LOCK:
                    _STATE["note"] = "selesai — tidak ada target tersisa"
                break
            if max_items and processed >= max_items:
                with _LOCK:
                    _STATE["note"] = "berhenti: batas max_items tercapai"
                break
            if sleep_s:
                _time.sleep(sleep_s)
    except Exception as e:
        with _LOCK:
            _STATE["last_error"] = str(e)[:300]
            _STATE["note"] = "error"
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        with _LOCK:
            _STATE["running"] = False
            _STATE["stop"] = False
            _STATE["finished_at"] = _now_iso()


def start_background_drain(connect_fn, chat_fn=None, chunk=25, sleep_s=0.0,
                           max_items=None, only_called=False):
    """Mulai drain draf-AI di thread daemon. Idempoten: jika sudah berjalan,
    kembalikan status tanpa memulai job kedua."""
    global _THREAD
    with _LOCK:
        if _STATE["running"]:
            d = dict(_STATE)
            d["ok"] = True
            d["already_running"] = True
            return d
        _STATE.update({
            "running": True, "stop": False, "started_at": _now_iso(),
            "finished_at": "", "done": 0, "ok": 0, "fail": 0, "locked": 0,
            "remaining": None, "target_awal": None, "note": "berjalan",
            "last_error": "", "only_called": bool(only_called),
        })
    ch = max(1, min(200, int(chunk or 25)))
    t = _threading.Thread(
        target=_drain_loop,
        args=(connect_fn, chat_fn, ch, float(sleep_s or 0), max_items, bool(only_called)),
        daemon=True,
    )
    _THREAD = t
    t.start()
    return {"ok": True, "started": True, "chunk": ch, "only_called": bool(only_called)}
