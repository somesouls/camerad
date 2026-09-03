# -*- coding: utf-8 -*-
"""voicebot/rag.py -- "RAG voicebot" dengan SUMBER TUNGGAL: intent + training phrase.

Berbeda dari RAG chatbot (yang menarik dari korpus peraturan/Q&A), OTAK voicebot
HANYA di-grounding ke basis intent voicebot sendiri (vb_intents): nama intent,
contoh training phrase, dan jawaban resmi (response). Retrieval me-reuse embedding
NLU (voicebot.nlu.top_matches). Komposisi jawaban memakai LLM lokal
(common.llm_client) dengan "gaya suara": ringkas, lisan, tanpa markdown/sitasi,
Bahasa Indonesia. Fail-soft di tiap tahap: bila LLM tak ada, pakai jawaban resmi
intent teratas; bila tak ada match sama sekali, pakai balasan fallback.

Selain answer() (jalur RAG untuk pertanyaan ekor-panjang), modul ini juga
menyediakan:
  - shorten() (poin 2b): meringkas jawaban intent STATIS menjadi versi lisan
    ringkas, TANPA mengubah fakta/angka. Hasil di-cache.
  - segment_steps() + guided_step_reply() (poin #2): memecah jawaban intent jadi
    langkah berurutan untuk 'jawaban menuntun' (guided walkthrough) dan menyusun
    balasan tiap langkah (akui selaan penelepon + sampaikan langkah berikutnya).
"""
import re

from voicebot import nlu as vb_nlu

_DEFAULT_SYSTEM = (
    "Anda adalah asisten suara (voicebot) berbahasa Indonesia untuk layanan pelanggan. "
    "Jawab HANYA berdasarkan DAFTAR PENGETAHUAN yang diberikan (kumpulan intent, contoh "
    "pertanyaan, dan jawaban resmi). DILARANG mengarang informasi di luar daftar itu. "
    "Jika pertanyaan tidak tercakup, katakan dengan sopan bahwa Anda belum memiliki "
    "informasinya lalu tawarkan untuk menghubungkan ke agen. "
    "GAYA JAWABAN untuk SUARA: ringkas 1-3 kalimat, bahasa lisan yang sopan dan jelas, "
    "tanpa poin bertanda, tanpa markdown, tanpa tautan atau sitasi, tanpa emoji. "
    "Bila perlu menuntun langkah, sebutkan singkat dan berurutan memakai kata "
    "'pertama', 'kemudian', 'terakhir'."
)


def _top_k(settings):
    try:
        return int((settings or {}).get("rag_top_k") or 5)
    except Exception:
        return 5


def _context(matches):
    blocks = []
    for i, m in enumerate(matches, 1):
        resp = (m.get("response") or "").strip()
        phs = m.get("phrases") or []
        ex = "; ".join([p for p in phs[:3] if p])
        blok = "PENGETAHUAN %d\nIntent: %s" % (i, m.get("intent") or "")
        if ex:
            blok += "\nContoh pertanyaan: %s" % ex
        if resp:
            blok += "\nJawaban resmi: %s" % resp
        blocks.append(blok)
    return "\n\n".join(blocks)


def answer(text, history=None, settings=None, k=None):
    """Jawab satu ucapan bersumber tunggal intent+training phrase.

    Kembalikan dict: {ok, jawaban, engine, intents, top_score}. Fail-soft.
    """
    settings = settings or {}
    text = (text or "").strip()
    res = {"ok": False, "jawaban": "", "engine": "rag", "intents": [], "top_score": 0.0}
    if not text:
        res["jawaban"] = settings.get("fallback_reply") or ""
        return res
    k = k or _top_k(settings)
    try:
        matches = vb_nlu.top_matches(text, k=k)
    except Exception as e:  # noqa: BLE001
        print("[voicebot.rag] retrieval gagal: %s" % e, flush=True)
        matches = []
    res["intents"] = [m.get("intent") for m in matches]
    res["top_score"] = round(float(matches[0]["score"]), 3) if matches else 0.0

    if not matches:
        res["jawaban"] = settings.get("fallback_reply") or (
            "Maaf, saya belum memiliki informasi untuk pertanyaan itu. "
            "Apakah Anda ingin saya hubungkan ke agen kami?")
        return res

    sysmsg = (settings.get("rag_system") or _DEFAULT_SYSTEM) + (
        "\n\n=== DAFTAR PENGETAHUAN (satu-satunya sumber) ===\n" + _context(matches))
    msgs = []
    for h in (history or [])[-4:]:
        if isinstance(h, dict):
            if h.get("user"):
                msgs.append({"role": "user", "content": h.get("user", "")})
            if h.get("bot"):
                msgs.append({"role": "assistant", "content": h.get("bot", "")})
    msgs.append({"role": "user", "content": text})

    try:
        import common.llm_client as llm
        ans = llm.chat(msgs, system=sysmsg, max_new_tokens=220, temperature=0.3)
        ans = (ans or "").strip()
        if ans:
            res["ok"] = True
            res["jawaban"] = ans
            return res
    except Exception as e:  # noqa: BLE001
        print("[voicebot.rag] LLM gagal: %s" % e, flush=True)

    # LLM tak tersedia -> pakai jawaban resmi intent teratas apa adanya.
    res["jawaban"] = (matches[0].get("response") or settings.get("fallback_reply")
                      or "Maaf, saya belum memiliki informasi untuk pertanyaan itu.")
    return res


# ------------------------------------------------------------------ peringkas (2b)
_DEFAULT_SHORTEN_SYSTEM = (
    "Anda meringkas jawaban call center untuk DIBACAKAN sebagai suara dalam Bahasa "
    "Indonesia. Persingkat teks menjadi 1-2 kalimat lisan yang sopan, jelas, dan "
    "langsung ke inti. DILARANG mengubah, menambah, atau menghapus fakta, angka, "
    "nominal, syarat, nama, atau langkah penting; pertahankan seluruh informasi wajib. "
    "Buang hanya kata berlebih, basa-basi, dan pengulangan. Tanpa markdown, tanpa poin "
    "bertanda, tanpa tautan atau sitasi, tanpa emoji. Bila teks sudah ringkas, kembalikan "
    "apa adanya. Keluarkan HANYA teks jawaban akhir tanpa penjelasan tambahan."
)

_SHORTEN_CACHE = {}
_SHORTEN_CACHE_MAX = 500


def _shorten_min_chars(settings):
    try:
        return int((settings or {}).get("intent_shorten_min_chars") or 160)
    except Exception:
        return 160


def shorten(text, settings=None):
    """Persingkat jawaban intent STATIS jadi versi lisan ringkas via LLM lokal.

    Dipakai jalur 'act'/konfirmasi di engine agar jawaban match-intent ikut ringkas
    seperti gaya RAG. Fail-soft + cache: kembalikan teks ASLI bila fitur mati, teks
    sudah pendek, atau LLM gagal. Fakta/angka dijaga lewat prompt (bukan mengarang).
    """
    settings = settings or {}
    text = (text or "").strip()
    if not text:
        return text
    if str(settings.get("intent_shorten_enabled", "0")) == "0":
        return text
    if len(text) < _shorten_min_chars(settings):
        return text
    sysmsg = settings.get("intent_shorten_system") or _DEFAULT_SHORTEN_SYSTEM
    ck = (text, sysmsg)
    if ck in _SHORTEN_CACHE:
        return _SHORTEN_CACHE[ck]
    try:
        import common.llm_client as llm
        ans = llm.chat([{"role": "user", "content": text}], system=sysmsg,
                       max_new_tokens=200, temperature=0.2)
        ans = (ans or "").strip()
        if ans:
            if len(_SHORTEN_CACHE) >= _SHORTEN_CACHE_MAX:
                _SHORTEN_CACHE.clear()
            _SHORTEN_CACHE[ck] = ans
            return ans
    except Exception as e:  # noqa: BLE001
        print("[voicebot.rag] shorten gagal: %s" % e, flush=True)
    return text


def reset_shorten_cache():
    _SHORTEN_CACHE.clear()


# ------------------------------------------------- jawaban menuntun / guided (#2)
_STEP_SPLIT_NUM = re.compile(r"(?:(?<=\s)|^)\d+[\.\)]\s+")
_SENT_SPLIT = re.compile(r"(?<=[\.\?\!])\s+")

_DEFAULT_GUIDED_SYSTEM = (
    "Anda asisten suara call center berbahasa Indonesia yang sedang MENUNTUN "
    "penelepon langkah demi langkah. Anda diberi: ucapan/selaan penelepon, LANGKAH "
    "BERIKUTNYA yang wajib disampaikan, dan langkah sebelumnya sebagai konteks. "
    "Tugas Anda: akui singkat selaan penelepon lalu sampaikan LANGKAH BERIKUTNYA itu "
    "secara utuh dengan bahasa lisan yang sopan dan jelas. DILARANG mengubah, "
    "menambah, atau menghapus fakta, angka, alamat email, nominal, atau syarat pada "
    "langkah tersebut. Jangan melompati atau mengarang langkah. Ringkas 1-3 kalimat, "
    "tanpa markdown, tanpa poin bertanda, tanpa emoji. Keluarkan HANYA kalimat untuk "
    "dibacakan."
)


def _merge_short_steps(parts, min_len=24):
    """Rapikan daftar langkah: buang penanda bullet & gabungkan fragmen pendek."""
    out = []
    for p in parts:
        p = (p or "").strip().strip("-*\u2022\t ").strip()
        if not p:
            continue
        if out and len(p) < min_len:
            out[-1] = (out[-1] + " " + p).strip()
        else:
            out.append(p)
    if len(out) >= 2 and len(out[0]) < min_len:
        out[1] = (out[0] + " " + out[1]).strip()
        out = out[1:]
    return out


def segment_steps(text, settings=None):
    """Pecah jawaban intent jadi daftar langkah berurutan (deterministik).

    Prioritas pemisah: baris baru -> penanda bernomor ('1.'/'2)') -> kalimat.
    Fragmen pendek digabung ke tetangganya agar tiap langkah bermakna. Bila teks
    tak bisa dipecah, kembalikan [text] (1 langkah).
    """
    t = (text or "").strip()
    if not t:
        return []
    lines = [l for l in re.split(r"[\r\n]+", t) if l.strip()]
    if len(lines) >= 2:
        steps = _merge_short_steps(lines)
        if len(steps) >= 2:
            return steps
    parts = [p for p in _STEP_SPLIT_NUM.split(t) if p and p.strip()]
    if len(parts) >= 2:
        steps = _merge_short_steps(parts)
        if len(steps) >= 2:
            return steps
    sents = [s for s in _SENT_SPLIT.split(t) if s and s.strip()]
    if len(sents) >= 2:
        steps = _merge_short_steps(sents)
        if len(steps) >= 2:
            return steps
    return [t]


def guided_step_reply(user_text, next_step, prev_step=None, settings=None):
    """Susun kalimat menuntun: akui selaan penelepon + sampaikan langkah berikut.

    Hybrid: langkah sudah dipotong deterministik (segment_steps); LLM hanya
    memperhalus transisi agar terasa nyambung dengan selaan penelepon. Fail-soft:
    kembalikan teks langkah apa adanya bila guided_llm_blend mati atau LLM gagal.
    """
    settings = settings or {}
    nxt = (next_step or "").strip()
    if not nxt:
        return ""
    if str(settings.get("guided_llm_blend", "1")) == "0":
        return nxt
    sysmsg = settings.get("guided_step_system") or _DEFAULT_GUIDED_SYSTEM
    prompt = ""
    if prev_step:
        prompt += "LANGKAH SEBELUMNYA (konteks, jangan diulang): " + prev_step.strip() + "\n"
    prompt += "SELAAN PENELEPON: " + (user_text or "").strip() + "\n"
    prompt += "LANGKAH BERIKUTNYA (wajib sampaikan utuh): " + nxt
    try:
        import common.llm_client as llm
        ans = llm.chat([{"role": "user", "content": prompt}], system=sysmsg,
                       max_new_tokens=200, temperature=0.3)
        ans = (ans or "").strip()
        if ans:
            return ans
    except Exception as e:  # noqa: BLE001
        print("[voicebot.rag] guided_step gagal: %s" % e, flush=True)
    return nxt
