# -*- coding: utf-8 -*-
"""voicebot/engine.py -- orkestrasi voice loop (Mode A, turn-based).

    audio/teks -> STT -> [dialog manager] -> NLU hybrid
               -> (konfirmasi-dulu | jawaban menuntun | respons intent | konfirmasi | RAG)
               -> keputusan hand-off -> TTS -> log turn.

Dialog manager (voicebot.dialog) menambah lapisan percakapan bergaya agen:
  - Perintah global: ulangi / selesai / bicara dengan agen (selalu aktif).
  - Tier confidence: act (>= ambang) / confirm (menengah) / rag (rendah).
  - Konfirmasi selektif + resolusi ya/tidak (state pending_confirm per sesi).
  - Digression: klasifikasi ulang tiap giliran + simpan intent terakhir (resume).
  - Sapaan + filler acknowledgment (pre-gen, diambil klien saat menunggu).

Konfirmasi-dulu tanpa LLM (#1): bila 'confirm_first' aktif dan NLU menemukan intent
(>= confirm_min), mesin LANGSUNG membaca ulang kalimat konfirmasi deterministik
(confirm_label intent / fallback dari nama intent) TANPA memanggil LLM, sambil
MENYIAPKAN jawaban lengkap (peringkas 2b) di background dan menyimpannya di
pending_confirm['prepared']. Setelah penelepon menjawab 'ya', jawaban yang sudah
siap langsung dibacakan (terasa instan). Bila penelepon menolak ('bukan, saya mau ...'),
ucapan yang sama diklasifikasi ulang: bila cocok intent lain -> konfirmasi intent baru.

Jawaban menuntun / guided walkthrough (#2): bila 'guided_enabled' aktif dan jawaban
intent bisa dipecah >= guided_min_steps langkah (voicebot.rag.segment_steps), jawaban
tidak diberikan sekaligus melainkan BERTAHAP (satu langkah tiap giliran), disimpan di
sess['active_flow']={intent,steps,idx,raw,score}. Tiap selaan penelepon: (a) bila
menandakan buntu (guided_handoff_triggers) -> tawarkan agen (pending_confirm mode
'handoff_offer'); (b) bila cocok intent lain (>= threshold) -> pindah konteks; (c)
selain itu -> lanjut ke langkah berikutnya (rag.guided_step_reply memperhalus transisi,
fail-soft ke teks langkah). Langkah terakhir ditutup dengan guided_closing.

Salam penutup + pemicu 'terima kasih' (#4): selain perintah 'selesai' eksplisit
(cmd_end), ucapan penutup LUNAK (mis. 'terima kasih' yang berdiri sendiri / pendek)
memicu penutupan (action='end') lewat vb_dialog.wants_closing -- dengan GUARD
ucapan-berdiri-sendiri (<= closing_trigger_max_words kata) supaya 'terima kasih' di
tengah kalimat sopan tak salah menutup. Transkrip yang cocok pola HALUSINASI STT
saat senyap ('terima kasih telah menonton') DIBUANG lebih dulu (diperlakukan seperti
tak ada ucapan). Salam penutup (closing_reply) dibacakan APA ADANYA / verbatim.

STT prediktif / biasing (#5): sebelum STT, engine menyusun (initial_prompt, hotwords)
domain via vb_stt.build_bias(settings) -- dari istilah manual + kamus pelafalan
(vb_lexicon) + nama intent aktif -- lalu meneruskannya ke vb_stt.transcribe_bytes
supaya faster-whisper condong ke kosakata domain (NPWP/EFIN/SPT/dll.). Bias NLU
dilengkapi dengan meneruskan 'settings' ke vb_nlu.classify (lihat nlu_bias_map di
voicebot.nlu): bila kata kunci tertentu muncul, skor intent terkait dinaikkan. Semua
fail-soft: bila biasing gagal disusun, STT/NLU tetap jalan tanpa bias.

Peringkas jawaban intent (2b): bila 'intent_shorten_enabled', jawaban match-intent
dilewatkan vb_rag.shorten() agar ikut ringkas gaya suara (cache + fail-soft;
fakta/angka dijaga). Jalur RAG memang sudah ringkas by design.

JARING PENGAMAN PENANDA 'spoken-first' (#3b): respons intent format baru memakai
penanda [INTI]/[TANYA]/[CABANG: ..]/[ESKALASI]/[TAUTAN] yang HANYA untuk menyusun
jawaban, bukan untuk dibacakan. Sebelum TTS, engine SELALU membersihkan penanda ini
lewat _clean_spoken() (apa pun jalurnya: shorten/guided/RAG/konfirmasi), jadi walau
LLM peringkas mati/gagal, penanda tak pernah ikut terucap. Respons berpenanda juga
tidak dipecah guided deterministik (butuh pemilihan cabang oleh LLM) -> diserahkan
ke jalur peringkas yang promptnya memang menangani [TANYA]/[CABANG].

Sentimen & valensi (Poin 3.3, OPSIONAL): bila 'sentiment_enabled' aktif, transkrip
dianalisis ringan (voicebot.sentiment) untuk menyesuaikan CARA menjawab: saat
penelepon terdengar frustrasi, jawaban biasa diawali pengakuan empatik singkat dan
agen ditawarkan lebih cepat (lihat _check_handoff). Default MATI -> tanpa efek.

Pra-hasil jawaban (Poin 3.2, OPSIONAL): pregen_answers() menghangatkan cache shorten
+ TTS untuk frasa yang sering dipakai (salam/penutup/filler/konfirmasi/jawaban intent)
supaya giliran awal tak menanggung waktu itu. Default MATI ('pregen_enabled').

Mode streaming (reply_on_empty=False): bila STT tidak menangkap ucapan apa pun,
talk() mengembalikan no_speech=True TANPA membalas/menyintesis apa pun, tanpa
menaikkan fallback_streak, dan tanpa dicatat -- supaya noise/gema tidak memicu
'maaf' berulang atau handoff palsu di Mode B.

Latency: talk() mengembalikan 'elapsed_ms' (total) + 'timings' dengan rincian
stt_ms / think_ms / tts_ms / total_ms untuk keperluan evaluasi (Lab & Mode B).

Sesi disimpan in-memory (cukup untuk tahap konsep, 1 proses). Semua komponen
berat di-impor LAZY + fail-soft. Reuse: voicebot.stt (faster-whisper),
voicebot.rag (RAG bersumber tunggal intent+training phrase), common.llm_client
(cadangan), handoff.routing_db (perutean layanan).
"""
import re
import time
import uuid
import base64
import random
import datetime as _dt

from voicebot import config_db as cfg
from voicebot import nlu as vb_nlu
from voicebot import stt as vb_stt
from voicebot import tts as vb_tts
from voicebot import rag as vb_rag
from voicebot import dialog as vb_dialog
from voicebot import sentiment as vb_sentiment

_SESSIONS = {}


def _now_iso():
    return _dt.datetime.now().isoformat(timespec="seconds")


# --- pembersih penanda format 'spoken-first' (#3b) ---------------------------
# Respons intent format baru memakai penanda [INTI]/[TANYA]/[CABANG: ..]/
# [ESKALASI]/[TAUTAN]. Penanda ini untuk MENYUSUN jawaban, TIDAK boleh ikut
# TERUCAP. _clean_spoken() membuang semua penanda + seluruh baris [TAUTAN] (URL
# untuk layar) sebagai jaring pengaman di HILIR (apa pun jalurnya:
# shorten/guided/RAG/konfirmasi), jadi walau LLM peringkas mati/gagal, penanda
# tetap tak pernah dibacakan. _has_markers() dipakai agar respons berformat ini
# TIDAK dipecah guided deterministik (butuh pemilihan cabang oleh LLM).
_MARK_TAUTAN_LINE = re.compile(r"(?im)^[ \t]*\[TAUTAN\][^\n]*$")
_MARK_CABANG = re.compile(r"(?i)\[CABANG:[^\]]*\]")
_MARK_TAG = re.compile(r"(?i)\[(?:INTI|TANYA|ESKALASI|TAUTAN)\]")


def _has_markers(text):
    """True bila teks memakai penanda format 'spoken-first'."""
    try:
        t = (text or "").upper()
        return ("[INTI]" in t or "[TANYA]" in t or "[CABANG" in t
                or "[ESKALASI]" in t or "[TAUTAN]" in t)
    except Exception:
        return False


def _clean_spoken(text):
    """Buang penanda [INTI]/[TANYA]/[CABANG:..]/[ESKALASI] & seluruh baris
    [TAUTAN] supaya tak pernah ikut terucap. No-op untuk teks tanpa penanda;
    fail-soft (kembalikan input apa adanya bila error / hasil jadi kosong)."""
    try:
        t = text or ""
        if "[" not in t:
            return text
        t = _MARK_TAUTAN_LINE.sub("", t)
        t = _MARK_CABANG.sub(" ", t)
        t = _MARK_TAG.sub(" ", t)
        t = re.sub(r"[ \t]+", " ", t)
        t = re.sub(r"\n{3,}", "\n\n", t)
        t = "\n".join(ln.strip() for ln in t.split("\n"))
        return t.strip() or text
    except Exception:
        return text


def create_session():
    sid = uuid.uuid4().hex
    _SESSIONS[sid] = {
        "created_at": _now_iso(),
        "history": [],
        "fallback_streak": 0,
        "last_answer": "",
        "last_intent": None,
        "pending_confirm": None,
        "active_flow": None,
    }
    out = {"session_id": sid, "created_at": _SESSIONS[sid]["created_at"]}
    try:
        settings = cfg.get_settings()
        if vb_dialog.enabled(settings):
            out["greeting"] = vb_dialog.greeting(settings)
    except Exception:
        pass
    return out


def end_session(sid):
    _SESSIONS.pop(sid, None)
    return {"ok": True}


def _get_session(sid):
    if not sid or sid not in _SESSIONS:
        s = create_session()
        return s["session_id"], _SESSIONS[s["session_id"]]
    return sid, _SESSIONS[sid]


# ------------------------------------------------ input telephony 8k (Poin 2A)
def _telephony_band_enabled(settings):
    """Simulasikan kanal telepon 8 kHz pada INPUT STT? Setting
    `stt_telephony_band` (default '0' = mati). Berguna menguji ketahanan STT pada
    kualitas telephony (mis. Avaya) tanpa integrasi penuh. Fail-soft.
    """
    try:
        return str((settings or {}).get("stt_telephony_band", "0")) not in (
            "0", "false", "False", "no", "NO", "")
    except Exception:
        return False


def _band_limit_8k(audio_bytes):
    """Turunkan WAV ke 8 kHz mono lalu naikkan lagi ke 16 kHz (band-limit ala
    telepon) untuk menguji STT pada kualitas 8 kHz. Fail-soft: kembalikan input
    apa adanya bila audioop tak ada / bukan WAV PCM16 / gagal.
    """
    try:
        import io as _io
        import wave as _wave
        import audioop as _audioop
    except Exception:
        return audio_bytes
    try:
        with _wave.open(_io.BytesIO(audio_bytes), "rb") as r:
            nch = r.getnchannels()
            sw = r.getsampwidth()
            sr = r.getframerate()
            frames = r.readframes(r.getnframes())
        if sw != 2:
            return audio_bytes
        if nch and nch > 1:
            frames = _audioop.tomono(frames, 2, 0.5, 0.5)
        down, _s1 = _audioop.ratecv(frames, 2, 1, int(sr), 8000, None)
        up, _s2 = _audioop.ratecv(down, 2, 1, 8000, int(sr), None)
        buf = _io.BytesIO()
        w = _wave.open(buf, "wb")
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes(up)
        w.close()
        return buf.getvalue()
    except Exception as e:  # noqa: BLE001
        print("[voicebot.engine] band-limit 8k gagal (pakai asli): %s" % e, flush=True)
        return audio_bytes


def _shorten_intent(jawaban, settings):
    """Peringkas jawaban intent statis (2b) via RAG.shorten; fail-soft."""
    try:
        return vb_rag.shorten(jawaban, settings)
    except Exception as e:  # noqa: BLE001
        print("[voicebot.engine] shorten gagal: %s" % e, flush=True)
        return jawaban


def _seg_steps(raw, settings):
    """Pecah jawaban jadi langkah (guided #2) via RAG.segment_steps; fail-soft."""
    try:
        return vb_rag.segment_steps(raw, settings)
    except Exception as e:  # noqa: BLE001
        print("[voicebot.engine] segment gagal: %s" % e, flush=True)
        return [raw] if raw else []


def _start_guided(intent, raw, score, sess, settings):
    """Mulai alur MENUNTUN bila jawaban bisa dipecah >= guided_min_steps langkah.

    Bila memenuhi: set sess['active_flow'] dan kembalikan
    (kalimat_langkah_pertama, True). Bila hanya 1 langkah: kembalikan ('', False)
    -> pemanggil memakai jalur jawaban ringkas biasa.
    """
    # Respons berformat 'spoken-first' (penanda [CABANG]/[TANYA]/[INTI]) butuh
    # PEMILIHAN CABANG oleh LLM -> jangan dipecah guided deterministik (akan
    # mencampur semua cabang jadi tak nyambung). Serahkan ke jalur peringkas
    # (shorten) yang promptnya memang menangani [TANYA]/[CABANG].
    if _has_markers(raw):
        return "", False
    steps = _seg_steps(raw, settings)
    if len(steps) < vb_dialog.guided_min_steps(settings):
        return "", False
    sess["active_flow"] = {
        "intent": intent,
        "steps": steps,
        "idx": 0,
        "raw": raw,
        "score": float(score or 0.0),
    }
    sess["last_intent"] = intent
    sess["fallback_streak"] = 0
    intro = vb_dialog.guided_intro(settings)
    nudge = vb_dialog.guided_nudge(settings)
    jawaban = " ".join(x for x in [intro, steps[0], nudge] if x).strip()
    return jawaban, True


def _confirm_first_setup(cls, sess, settings, fb):
    """Susun giliran KONFIRMASI-DULU dari hasil klasifikasi 'cls'.

    Membaca ulang kalimat konfirmasi deterministik (tanpa LLM) + menyiapkan
    jawaban lengkap (peringkas 2b) untuk disimpan di pending_confirm['prepared'].
    Kembalikan dict field giliran: {intent, confidence, engine, jawaban, sumber, action}.
    """
    intent = cls.get("intent")
    confidence = float(cls.get("score") or 0.0)
    raw = (cls.get("response") or cfg.intent_response(intent) or fb)
    prepared = _shorten_intent(raw, settings)
    label = (cfg.intent_confirm_label(intent) or "").strip() \
        or vb_dialog.auto_confirm_label(intent)
    jawaban = vb_dialog.confirm_first_prompt(label, settings)
    sess["pending_confirm"] = {
        "intent": intent,
        "response": raw,
        "prepared": prepared,
        "score": confidence,
        "mode": "confirm_first",
    }
    sess["fallback_streak"] = 0
    return {
        "intent": intent,
        "confidence": confidence,
        "engine": cls.get("engine"),
        "jawaban": jawaban,
        "sumber": "dialog",
        "action": "confirm",
    }


def _llm_fallback(text, sess, settings):
    """Cadangan: jawaban terbuka via LLM lokal mentah (dipakai bila rag_enabled=0
    atau RAG voicebot mengembalikan kosong). Fail-soft."""
    try:
        import common.llm_client as llm
        try:
            names = [i["name"] for i in cfg.list_intents()]
        except Exception:
            names = []
        sysmsg = settings.get("llm_system") or ""
        if names:
            sysmsg += ("\nIntent yang tersedia: " + ", ".join(names[:50]) +
                       ". Jika pertanyaan cocok salah satu, jawab sesuai konteks itu.")
        msgs = []
        for h in sess["history"][-6:]:
            if h.get("user"):
                msgs.append({"role": "user", "content": h.get("user", "")})
            if h.get("bot"):
                msgs.append({"role": "assistant", "content": h["bot"]})
        msgs.append({"role": "user", "content": text})
        ans = llm.chat(msgs, system=sysmsg, max_new_tokens=256, temperature=0.4)
        return (ans or "").strip() or (settings.get("fallback_reply") or "")
    except Exception as e:  # noqa: BLE001
        print("[voicebot.engine] LLM fallback gagal: %s" % e, flush=True)
        return settings.get("fallback_reply") or ""


def _rag_answer(transkrip, sess, settings):
    """Jalur RAG voicebot (sumber tunggal intent+training phrase) + cadangan LLM.
    Kembalikan (jawaban, sumber)."""
    if str(settings.get("rag_enabled", "1")) != "0":
        jawaban = ""
        try:
            r = vb_rag.answer(transkrip, sess.get("history"), settings)
            jawaban = (r or {}).get("jawaban") or ""
        except Exception as e:  # noqa: BLE001
            print("[voicebot.engine] RAG gagal: %s" % e, flush=True)
            jawaban = ""
        if jawaban:
            return jawaban, "rag"
        return _llm_fallback(transkrip, sess, settings), "llm"
    return _llm_fallback(transkrip, sess, settings), "llm"


def _check_handoff(text, sess, settings, senti=None):
    """Putuskan apakah giliran ini perlu dialihkan ke agen + alasannya.

    Selain pemicu lama (kata kunci eksplisit, gagal paham beruntun, kecocokan
    intent layanan pada handoff.routing_db), kini ada jalur ADAPTIF-SENTIMEN
    (Poin 3.3): bila sentimen aktif dan penelepon terdengar FRUSTRASI beberapa
    giliran beruntun (>= sentiment_handoff_neg_streak), tawarkan agen lebih cepat.
    Jalur sentimen hanya aktif bila `senti` diberikan (fitur menyala).
    """
    tl = (text or "").lower()
    triggers = [t.strip().lower() for t in (settings.get("handoff_triggers") or "").split(",") if t.strip()]
    for t in triggers:
        if t and t in tl:
            return True, "permintaan eksplisit ke agen"
    try:
        maxfb = int(settings.get("handoff_max_fallback") or 2)
    except Exception:
        maxfb = 2
    if maxfb and sess.get("fallback_streak", 0) >= maxfb:
        return True, "gagal dipahami %d kali beruntun" % sess.get("fallback_streak", 0)
    if senti is not None and senti.get("frustrated"):
        try:
            need = int(settings.get("sentiment_handoff_neg_streak") or 2)
        except Exception:
            need = 2
        if need > 0 and int(sess.get("neg_streak", 0)) >= need:
            return True, ("penelepon terdengar frustrasi %d kali beruntun"
                          % int(sess.get("neg_streak", 0)))
    try:
        import handoff.routing_db as hrdb
        row = hrdb.match_routing(text)
        if row:
            return True, "cocok intent layanan: %s" % (row.get("top_intent") or "")
    except Exception:
        pass
    return False, ""


def get_filler(want_audio=True, index=None):
    """Ambil satu klip filler (teks + audio base64) untuk diputar klien saat
    jawaban asli sedang dihitung. Fail-soft."""
    settings = cfg.get_settings()
    if not vb_dialog.filler_enabled(settings):
        return {"enabled": False, "text": "", "audio_b64": None, "tts_error": None}
    arr = vb_dialog.fillers(settings)
    if not arr:
        return {"enabled": True, "text": "", "audio_b64": None, "tts_error": None}
    if index is None:
        text = random.choice(arr)
    else:
        try:
            text = arr[int(index) % len(arr)]
        except Exception:
            text = arr[0]
    audio_b64 = None
    tts_err = None
    if want_audio and text and str(settings.get("tts_enabled", "1")) != "0":
        wav, tts_err = vb_tts.synth(text)
        if wav:
            audio_b64 = base64.b64encode(wav).decode("ascii")
    return {"enabled": True, "text": text, "audio_b64": audio_b64, "tts_error": tts_err}


def pregen_answers(settings=None, want_audio=True, limit=None):
    """Pra-hasilkan (warm-up) jawaban & audio untuk frasa yang SERING dipakai
    supaya giliran awal tak menanggung waktu shorten/TTS (Poin 3.2).

    Yang dihangatkan: salam pembuka & penutup, teks filler, kalimat
    konfirmasi-dulu per intent, serta jawaban intent (versi ringkas bila
    intent_shorten aktif). Memanfaatkan cache TTS + cache shorten yang sudah ada,
    jadi aman dipanggil berulang. Semua fail-soft. Dikontrol setting
    `pregen_enabled` (default '0' = mati). Kembalikan ringkasan hitungan.
    """
    settings = settings or {}
    out = {"phrases": 0, "audio": 0, "errors": 0, "enabled": True}
    try:
        if str(settings.get("pregen_enabled", "0")) == "0":
            out["enabled"] = False
            return out
    except Exception:
        out["enabled"] = False
        return out
    tts_on = bool(want_audio) and str(settings.get("tts_enabled", "1")) != "0"

    def _warm(text):
        text = (text or "").strip()
        if not text:
            return
        out["phrases"] += 1
        if not tts_on:
            return
        try:
            wav, err = vb_tts.synth(text)
            if wav:
                out["audio"] += 1
            elif err:
                out["errors"] += 1
        except Exception:
            out["errors"] += 1

    try:
        if vb_dialog.enabled(settings):
            _warm(vb_dialog.greeting(settings))
            _warm(vb_dialog.closing_reply(settings))
            for f in (vb_dialog.fillers(settings) or []):
                _warm(f)
        try:
            intents = cfg.list_intents()
        except Exception:
            intents = []
        if limit:
            try:
                intents = intents[:int(limit)]
            except Exception:
                pass
        for it in (intents or []):
            name = it.get("name") if isinstance(it, dict) else None
            if not name:
                continue
            try:
                label = (cfg.intent_confirm_label(name) or "").strip() \
                    or vb_dialog.auto_confirm_label(name)
                _warm(vb_dialog.confirm_first_prompt(label, settings))
            except Exception:
                out["errors"] += 1
            try:
                raw = cfg.intent_response(name) or ""
                if raw:
                    _warm(_shorten_intent(raw, settings))
            except Exception:
                out["errors"] += 1
    except Exception as e:  # noqa: BLE001
        print("[voicebot.engine] pregen gagal: %s" % e, flush=True)
    print("[voicebot.engine] pregen selesai: %r" % out, flush=True)
    return out


def talk(session_id=None, text=None, audio_bytes=None, audio_filename="audio.wav",
         want_audio=True, reply_on_empty=True):
    """Satu giliran percakapan. Kembalikan dict respons (JSON-able).

    reply_on_empty=False (Mode B streaming): bila STT tak menangkap ucapan apa pun,
    kembalikan no_speech=True tanpa membalas/menyintesis apa pun (hindari 'maaf'
    berulang dari noise) dan tanpa menaikkan fallback_streak / mencatat turn.
    """
    t0 = time.time()
    settings = cfg.get_settings()
    sid, sess = _get_session(session_id)
    dlg_on = vb_dialog.enabled(settings)

    transkrip = (text or "").strip()
    stt_err = None
    if not transkrip and audio_bytes:
        if str(settings.get("stt_enabled", "1")) != "0":
            # STT prediktif (#5): biasakan dekode ke kosakata domain (fail-soft).
            init_prompt = hotwords = None
            try:
                init_prompt, hotwords = vb_stt.build_bias(settings)
            except Exception:
                init_prompt = hotwords = None
            # band-limit 8 kHz opsional (Poin 2A) untuk simulasi kanal telepon.
            stt_audio = audio_bytes
            if _telephony_band_enabled(settings):
                stt_audio = _band_limit_8k(audio_bytes)
            tr = vb_stt.transcribe_bytes(stt_audio, audio_filename,
                                         lang=settings.get("stt_lang") or "id",
                                         initial_prompt=init_prompt, hotwords=hotwords)
            transkrip = tr.get("text") or ""
            if not tr.get("ok"):
                stt_err = tr.get("error")
        else:
            stt_err = "STT dimatikan di konfigurasi"

    # penanda waktu: STT selesai (untuk rincian latency evaluasi)
    t_stt = time.time()

    # #4 GUARD halusinasi STT: transkrip yang cocok pola halusinasi saat senyap
    # (mis. 'terima kasih telah menonton') DIBUANG -> diperlakukan seperti tak ada
    # ucapan, supaya tidak memicu balasan maupun penutupan palsu.
    if (transkrip and dlg_on and vb_dialog.closing_enabled(settings)
            and vb_dialog.is_stt_hallucination(transkrip, settings)):
        print("[voicebot.engine] transkrip halusinasi STT diabaikan: %r" % transkrip,
              flush=True)
        transkrip = ""

    # Mode streaming: STT tak menangkap ucapan -> jangan membalas apa pun. Tidak
    # menaikkan fallback_streak & tidak dicatat, supaya noise/gema tak memicu
    # 'maaf' berulang maupun handoff palsu di Mode B.
    if not transkrip and not reply_on_empty:
        total_ms = int((time.time() - t0) * 1000)
        return {
            "session_id": sid,
            "transkrip": "",
            "intent": None,
            "confidence": 0.0,
            "sumber": "none",
            "tier": None,
            "engine": None,
            "jawaban_teks": "",
            "jawaban_audio_b64": None,
            "action": "noop",
            "resume_suggestion": None,
            "handoff": None,
            "stt_error": stt_err,
            "tts_error": None,
            "no_speech": True,
            "elapsed_ms": total_ms,
            "timings": {
                "stt_ms": int((t_stt - t0) * 1000),
                "think_ms": 0,
                "tts_ms": 0,
                "total_ms": total_ms,
            },
        }

    # Sentimen/valensi ringan (Poin 3.3): sinyal murah untuk menyesuaikan CARA
    # menjawab (empati + tawar agen lebih cepat saat frustrasi). Opsional & fail-soft:
    # bila 'sentiment_enabled' mati, senti tetap None dan tak ada efek apa pun.
    senti = None
    if transkrip and vb_sentiment.enabled(settings):
        senti = vb_sentiment.analyze(transkrip, settings)
        sess["last_sentiment"] = senti
        if senti.get("frustrated"):
            sess["neg_streak"] = int(sess.get("neg_streak", 0)) + 1
        else:
            sess["neg_streak"] = 0

    try:
        threshold = float(settings.get("threshold") or 0.6)
    except Exception:
        threshold = 0.6

    # --- keadaan awal giliran ---
    cls = {"intent": None, "score": 0.0, "response": "", "engine": "none"}
    intent = None
    confidence = 0.0
    engine = None
    sumber = "nlu"
    jawaban = ""
    action = "reply"
    tier = None
    resume_suggestion = None
    do_handoff = False
    handoff = None
    guided_turn = False
    fb = settings.get("fallback_reply") or "Maaf, saya belum menangkap maksudnya."
    cfirst = dlg_on and vb_dialog.confirm_first_enabled(settings)
    guided_on = dlg_on and vb_dialog.guided_enabled(settings)
    cmin = vb_dialog.confirm_min(settings)

    if not transkrip:
        jawaban = fb if not stt_err else (settings.get("fallback_reply")
                  or "Maaf, suara tidak terdengar. Boleh diulang?")
        sumber = "nlu"
        sess["fallback_streak"] = sess.get("fallback_streak", 0) + 1
    else:
        cmd = vb_dialog.global_command(transkrip, settings) if dlg_on else None
        # salam penutup (#4): pemicu LUNAK (mis. 'terima kasih' berdiri sendiri)
        # -> perlakukan seperti perintah 'selesai' (bacakan salam penutup + tutup).
        if dlg_on and cmd is None and vb_dialog.wants_closing(transkrip, settings):
            cmd = "end"
        pending = sess.get("pending_confirm") if dlg_on else None
        flow = sess.get("active_flow") if dlg_on else None

        if cmd == "repeat":
            jawaban = sess.get("last_answer") or "Maaf, belum ada jawaban yang bisa saya ulang."
            intent = sess.get("last_intent")
            sumber = "dialog"
            action = "repeat"
        elif cmd == "end":
            jawaban = vb_dialog.closing_reply(settings)
            sumber = "dialog"
            action = "end"
            sess["pending_confirm"] = None
            sess["active_flow"] = None
        elif cmd == "handoff":
            jawaban = vb_dialog.handoff_reply(settings)
            sumber = "dialog"
            sess["pending_confirm"] = None
            sess["active_flow"] = None
        elif pending and vb_dialog.is_affirmative(transkrip, settings):
            if pending.get("mode") == "handoff_offer":
                # penelepon setuju dialihkan ke agen (dari alur menuntun)
                sess["pending_confirm"] = None
                sess["active_flow"] = None
                jawaban = vb_dialog.handoff_reply(settings)
                intent = pending.get("intent")
                sumber = "dialog"
                action = "handoff"
                do_handoff = True
                parts = [("U: " + (h.get("user") or "")) for h in sess["history"][-3:]]
                parts.append("U: " + transkrip)
                handoff = {"reason": "penelepon setuju dialihkan ke agen (menuntun)",
                           "ringkasan": (" | ".join(parts))[:1000]}
            else:
                # penelepon membenarkan tebakan intent (konfirmasi-dulu). Bila jawaban
                # bisa dipecah beberapa langkah -> MULAI alur menuntun; jika tidak,
                # bacakan jawaban yang SUDAH disiapkan (terasa instan).
                ci = pending.get("intent")
                raw = (pending.get("response") or cfg.intent_response(ci) or fb)
                confidence = float(pending.get("score") or 0.0)
                intent = ci
                sumber = "nlu"
                sess["pending_confirm"] = None
                sess["fallback_streak"] = 0
                sess["last_intent"] = ci
                started = False
                if guided_on:
                    jawaban, started = _start_guided(ci, raw, confidence, sess, settings)
                    guided_turn = started
                if not started:
                    jawaban = pending.get("prepared") or _shorten_intent(raw, settings)
        elif pending and vb_dialog.is_negative(transkrip, settings):
            if pending.get("mode") == "handoff_offer":
                # penelepon menolak dialihkan ke agen -> tutup tawaran, tetap siap bantu.
                sess["pending_confirm"] = None
                sal = vb_dialog.salutation(settings)
                suf = (", " + sal) if (sal and str(settings.get("salutation_enabled", "1")) != "0") else ""
                jawaban = "Baik, tidak jadi ya. Ada lagi yang bisa saya bantu%s?" % suf
                sumber = "dialog"
                action = "reply"
            else:
                # penelepon menolak tebakan. Coba tangkap topik BARU dari ucapan yang
                # sama (mis. \"bukan, saya mau aktivasi EFIN\") -> konfirmasi intent baru.
                sess["pending_confirm"] = None
                recls = vb_nlu.classify(transkrip, settings=settings)
                if cfirst and recls.get("intent") and float(recls.get("score") or 0.0) >= cmin:
                    r = _confirm_first_setup(recls, sess, settings, fb)
                    intent, confidence, engine = r["intent"], r["confidence"], r["engine"]
                    jawaban, sumber, action = r["jawaban"], r["sumber"], r["action"]
                else:
                    jawaban = "Baik, mohon sampaikan kembali maksud Anda dengan kalimat lain."
                    sumber = "dialog"
                    action = "clarify"
        elif flow:
            # --- SEDANG DALAM ALUR MENUNTUN (guided walkthrough #2) ---
            guided_turn = True
            steps = flow.get("steps") or []
            idx = int(flow.get("idx") or 0)
            intent = flow.get("intent")
            confidence = float(flow.get("score") or 0.0)
            sess["fallback_streak"] = 0
            if vb_dialog.wants_handoff_in_flow(transkrip, settings):
                # penelepon buntu -> tawarkan agen (dikonfirmasi giliran berikutnya)
                sess["active_flow"] = None
                sess["pending_confirm"] = {"mode": "handoff_offer", "intent": intent}
                jawaban = vb_dialog.guided_handoff_offer(settings)
                sumber = "dialog"
                action = "confirm"
            else:
                recls = vb_nlu.classify(transkrip, settings=settings)
                r_intent = recls.get("intent")
                r_conf = float(recls.get("score") or 0.0)
                if r_intent and r_intent != intent and r_conf >= threshold:
                    # topik BARU (skor tinggi) -> keluar alur, mulai konteks baru
                    sess["active_flow"] = None
                    if cfirst:
                        r = _confirm_first_setup(recls, sess, settings, fb)
                        intent, confidence, engine = r["intent"], r["confidence"], r["engine"]
                        jawaban, sumber, action = r["jawaban"], r["sumber"], r["action"]
                    else:
                        raw2 = (recls.get("response") or cfg.intent_response(r_intent) or fb)
                        intent, confidence, engine = r_intent, r_conf, recls.get("engine")
                        started2 = False
                        if guided_on:
                            jawaban, started2 = _start_guided(r_intent, raw2, r_conf, sess, settings)
                        if not started2:
                            jawaban = _shorten_intent(raw2, settings)
                        sumber = "nlu"
                        sess["last_intent"] = r_intent
                else:
                    # lanjut ke langkah berikutnya
                    nxt_idx = idx + 1
                    if nxt_idx < len(steps):
                        prev_step = steps[idx]
                        nxt_step = steps[nxt_idx]
                        body = vb_rag.guided_step_reply(transkrip, nxt_step, prev_step, settings)
                        flow["idx"] = nxt_idx
                        if nxt_idx >= len(steps) - 1:
                            # langkah terakhir -> tutup alur
                            jawaban = (body + " " + vb_dialog.guided_closing(settings)).strip()
                            sess["active_flow"] = None
                        else:
                            nudge = vb_dialog.guided_nudge(settings)
                            jawaban = (body + ((" " + nudge) if nudge else "")).strip()
                        sumber = "dialog"
                        action = "reply"
                    else:
                        # sudah di langkah terakhir sebelumnya -> tutup alur
                        jawaban = vb_dialog.guided_closing(settings)
                        sess["active_flow"] = None
                        sumber = "dialog"
                        action = "reply"
        else:
            if pending:
                sess["pending_confirm"] = None  # penelepon lanjut ke topik baru
            cls = vb_nlu.classify(transkrip, settings=settings)
            intent = cls.get("intent")
            confidence = float(cls.get("score") or 0.0)
            engine = cls.get("engine")
            tier = (vb_dialog.decide_tier(confidence, settings) if dlg_on
                    else ("act" if (intent and confidence >= threshold) else "rag"))

            if cfirst and intent and confidence >= cmin:
                # KONFIRMASI-DULU tanpa LLM: baca ulang intent + siapkan jawaban.
                r = _confirm_first_setup(cls, sess, settings, fb)
                intent, confidence, engine = r["intent"], r["confidence"], r["engine"]
                jawaban, sumber, action = r["jawaban"], r["sumber"], r["action"]
            elif tier == "act" and intent:
                raw = (cls.get("response") or cfg.intent_response(intent) or fb)
                sumber = "nlu"
                sess["fallback_streak"] = 0
                prev = sess.get("last_intent")
                started = False
                if guided_on:
                    # jawaban panjang -> sampaikan bertahap (jawaban menuntun #2)
                    jawaban, started = _start_guided(intent, raw, confidence, sess, settings)
                    guided_turn = started
                if not started:
                    jawaban = _shorten_intent(raw, settings)
                    # digression: tawaran resume intent sebelumnya bila berpindah
                    if (dlg_on and prev and prev != intent
                            and vb_dialog.resume_enabled(settings)):
                        resume_suggestion = vb_dialog.resume_prompt(prev, settings)
                        jawaban = (jawaban + " " + resume_suggestion).strip()
                    elif dlg_on and prev and prev != intent:
                        resume_suggestion = vb_dialog.resume_prompt(prev, settings)
                sess["last_intent"] = intent
            elif tier == "confirm" and intent:
                jawaban = vb_dialog.confirm_prompt(intent, settings)
                sess["pending_confirm"] = {
                    "intent": intent,
                    "response": (cls.get("response") or cfg.intent_response(intent)),
                    "score": confidence,
                }
                sumber = "dialog"
                action = "confirm"
            else:
                intent = None
                jawaban, sumber = _rag_answer(transkrip, sess, settings)
                sess["fallback_streak"] = sess.get("fallback_streak", 0) + 1

    # --- hand-off (dilewati untuk aksi dialog terminal & giliran menuntun) ---
    reason = ""
    if action in ("reply", "repeat") and not guided_turn:
        do_handoff, reason = _check_handoff(transkrip, sess, settings, senti)
        if do_handoff:
            action = "handoff"
            parts = [("U: " + (h.get("user") or "")) for h in sess["history"][-3:]]
            parts.append("U: " + transkrip)
            handoff = {"reason": reason, "ringkasan": (" | ".join(parts))[:1000]}

    # Empati adaptif (Poin 3.3): bila penelepon terdengar FRUSTRASI, awali jawaban
    # biasa dengan pengakuan singkat. Hanya untuk action 'reply'/'repeat' (bukan
    # konfirmasi/menuntun/penutup/handoff) dan tidak menimpa jawaban yang sudah
    # meminta maaf. Opsional (sentiment_empathy_enabled, default ON saat sentimen aktif).
    if (senti is not None and senti.get("frustrated") and jawaban
            and action in ("reply", "repeat")
            and str(settings.get("sentiment_empathy_enabled", "1")) != "0"):
        pref = (settings.get("sentiment_empathy_prefix")
                or "Mohon maaf atas ketidaknyamanannya.").strip()
        low = jawaban.lower()
        if pref and not (low.startswith("mohon maaf") or low.startswith("maaf")):
            jawaban = (pref + " " + jawaban).strip()

    # JARING PENGAMAN #3b: buang penanda 'spoken-first' ([INTI]/[TANYA]/[CABANG]/
    # [ESKALASI]/[TAUTAN]) agar TIDAK pernah ikut terucap, apa pun jalur di atas.
    # No-op untuk jawaban tanpa penanda; fail-soft.
    if jawaban:
        jawaban = _clean_spoken(jawaban)

    # simpan jawaban substantif utk perintah \"ulangi\"
    if sumber in ("nlu", "rag", "llm", "dialog") and jawaban and action not in ("confirm",):
        sess["last_answer"] = jawaban

    # penanda waktu: proses (STT + think) selesai, mulai TTS
    t_think = time.time()

    audio_b64 = None
    tts_err = None
    if want_audio and jawaban and str(settings.get("tts_enabled", "1")) != "0":
        wav, tts_err = vb_tts.synth(jawaban)
        if wav:
            audio_b64 = base64.b64encode(wav).decode("ascii")

    # penanda waktu: TTS selesai
    t_tts = time.time()

    sess["history"].append({"user": transkrip, "bot": jawaban})
    id_trace = sid + "-" + str(len(sess["history"]))
    try:
        cfg.log_turn({
            "session_id": sid, "id_trace": id_trace, "user_text": transkrip,
            "intent": intent, "confidence": confidence, "sumber": sumber,
            "bot_text": jawaban, "handoff": do_handoff,
        })
    except Exception:
        pass

    total_ms = int((time.time() - t0) * 1000)
    return {
        "session_id": sid,
        "transkrip": transkrip,
        "intent": intent,
        "confidence": round(confidence, 3),
        "sumber": sumber,
        "tier": tier,
        "engine": engine,
        "jawaban_teks": jawaban,
        "jawaban_audio_b64": audio_b64,
        "action": action,
        "resume_suggestion": resume_suggestion,
        "handoff": handoff,
        "sentimen": senti,
        "stt_error": stt_err,
        "tts_error": tts_err,
        "elapsed_ms": total_ms,
        "timings": {
            "stt_ms": int((t_stt - t0) * 1000),
            "think_ms": int((t_think - t_stt) * 1000),
            "tts_ms": int((t_tts - t_think) * 1000),
            "total_ms": total_ms,
        },
    }
