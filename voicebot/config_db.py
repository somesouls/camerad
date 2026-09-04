# -*- coding: utf-8 -*-
"""voicebot/config_db.py -- penyimpanan konfigurasi, intent, kamus, & log turn.

SQLite tunggal (env VOICEBOT_DB_FILE, default 'voicebot.db'). Tabel:
  vb_settings(key, value)                 -- konfigurasi mesin (ambang, prompt, dst.)
  vb_intents(id, name, phrases, response, confirm_label) -- intent + training phrase (NLU lokal)
  vb_lexicon(id, pattern, replacement...) -- kamus pelafalan (dipakai TTS)
  vb_turns(...)                           -- log tiap giliran

Backup/restore: export_config() mengembalikan snapshot lengkap (settings +
intents + lexicon) yang JSON-able; import_config(data, mode) menerapkannya
kembali (mode 'merge' menimpa+menambah, 'replace' mengosongkan intents & lexicon
dulu). Berguna bila konfigurasi tak sengaja ter-reset -- cukup impor cadangan.

Gagal-anggun: fungsi baca mengembalikan default bila DB bermasalah.
"""
import os
import re
import json
import sqlite3

DB_FILE = os.environ.get("VOICEBOT_DB_FILE") or "voicebot.db"

DEFAULT_SETTINGS = {
    "threshold": "0.6",
    "stt_lang": "id",
    # Ukuran model STT (faster-whisper). Kosong = pakai ENV AWE_STT_MODEL lalu
    # default kode (large-v3). Isi 'medium' / 'small' untuk latency lebih rendah.
    "stt_model": "",
    "stt_enabled": "1",
    "tts_enabled": "1",
    "llm_system": (
        "Anda adalah asisten suara call center berbahasa Indonesia. Jawab "
        "singkat, sopan, dan jelas untuk dibacakan. Bila tidak yakin, arahkan "
        "penelepon ke petugas."
    ),
    "handoff_triggers": (
        "bicara dengan agen, hubungkan ke petugas, mau bicara dengan orang, "
        "operator, customer service"
    ),
    "handoff_max_fallback": "2",
    "fallback_reply": (
        "Maaf, saya belum menangkap maksudnya. Boleh diulang dengan kalimat lain?"
    ),
    "rag_enabled": "1",
    "rag_top_k": "5",
    "pron_enabled": "1",
    "pron_spell_digits_min": "7",
    # --- dialog manager (#3) ---
    "dialog_enabled": "1",
    "confirm_min": "0.45",
    "salutation": "Kak",
    "salutation_enabled": "1",
    "greeting": (
        "Selamat datang di layanan kami. Ada yang bisa saya bantu, Kak?"
    ),
    "closing_reply": (
        "Baik, terima kasih sudah menghubungi kami. Selamat beraktivitas kembali."
    ),
    "handoff_reply": (
        "Baik, saya hubungkan Anda dengan agen kami. Mohon tunggu sebentar."
    ),
    "confirm_template": (
        "Mohon konfirmasi, apakah Anda menanyakan tentang {intent}?"
    ),
    "readback_template": "Saya ulangi ya, {text}. Apakah sudah benar?",
    "resume_template": (
        "Sebelumnya kita membahas {intent}. Mau lanjutkan itu setelah ini?"
    ),
    "resume_enabled": "0",
    # --- konfirmasi-dulu tanpa LLM (#1) ---
    # Saat NLU menemukan intent (>= confirm_min), mesin langsung membaca ulang
    # kalimat konfirmasi deterministik (tanpa LLM) memakai confirm_label intent,
    # sambil menyiapkan jawaban di background. Baru setelah penelepon menjawab
    # 'ya' jawaban lengkap dibacakan. {sal}=sapaan, {label}=confirm_label intent.
    "confirm_first": "1",
    "confirm_first_template": "Baik {sal}, saya konfirmasi, {label}",
    # --- jawaban menuntun / guided walkthrough (#2, hybrid) ---
    # Bila jawaban intent bisa dipecah >= guided_min_steps langkah, jawaban
    # disampaikan BERTAHAP (satu langkah tiap giliran) mengikuti selaan penelepon.
    # Langkah dipotong deterministik (voicebot.rag.segment_steps); tiap balasan
    # menuntun boleh diperhalus LLM (rag.guided_step_reply -> fail-soft ke teks
    # langkah apa adanya) bila guided_llm_blend aktif. Penelepon buntu -> tawar agen.
    "guided_enabled": "1",
    "guided_min_steps": "2",
    "guided_llm_blend": "1",
    "guided_intro_template": "Baik {sal}, saya bantu ya.",
    "guided_nudge_template": "Kalau sudah atau ada kendala, sampaikan saja ya, {sal}.",
    "guided_closing_template": (
        "Itu tadi langkah-langkahnya, {sal}. Ada lagi yang bisa saya bantu?"
    ),
    "guided_handoff_offer": (
        "Mohon maaf {sal}, untuk hal itu sepertinya perlu bantuan petugas kami. "
        "Mau saya hubungkan dengan agen?"
    ),
    "guided_handoff_triggers": (
        "tidak bisa dibantu, nggak bisa dibantu, gak bisa dibantu, tidak berhasil, "
        "masih gagal, tetap gagal, mentok, tidak bisa lagi, belum selesai juga"
    ),
    "guided_step_system": (
        "Anda asisten suara call center berbahasa Indonesia yang sedang MENUNTUN "
        "penelepon langkah demi langkah. Anda diberi: ucapan/selaan penelepon, "
        "LANGKAH BERIKUTNYA yang wajib disampaikan, dan langkah sebelumnya sebagai "
        "konteks. Tugas Anda: akui singkat selaan penelepon lalu sampaikan LANGKAH "
        "BERIKUTNYA itu secara utuh dengan bahasa lisan yang sopan dan jelas. "
        "DILARANG mengubah, menambah, atau menghapus fakta, angka, alamat email, "
        "nominal, atau syarat pada langkah tersebut. Jangan melompati atau mengarang "
        "langkah. Ringkas 1-3 kalimat, tanpa markdown, tanpa poin bertanda, tanpa "
        "emoji. Keluarkan HANYA kalimat untuk dibacakan."
    ),
    "cmd_repeat": "ulangi, tolong ulangi, ulangi lagi, bisa diulang, ulang",
    "cmd_end": (
        "selesai, sudah cukup, cukup, tutup, sudah selesai, terima kasih sudah cukup"
    ),
    "affirmations": (
        "ya, iya, betul, benar, ya benar, betul sekali, benar sekali, oke, ok, "
        "iya betul, benar begitu"
    ),
    "negations": (
        "tidak, bukan, salah, tidak benar, bukan itu, nggak, enggak, gak, "
        "bukan begitu"
    ),
    # --- salam penutup + pemicu 'terima kasih' (#4) ---
    # Selain perintah 'selesai' (cmd_end), penelepon sering menutup percakapan
    # dengan ucapan LUNAK seperti 'terima kasih'/'makasih'/'sekian'. closing_triggers
    # mendeteksi ini, TAPI hanya bila ucapan BERDIRI SENDIRI / pendek
    # (<= closing_trigger_max_words kata) supaya 'terima kasih' di tengah kalimat
    # sopan (\"oh terima kasih, tapi saya masih mau tanya ...\") TIDAK memicu penutupan.
    # closing_hallucination_patterns = pola HALUSINASI STT saat senyap (mis.
    # 'terima kasih telah menonton') yang WAJIB diabaikan: tak dibalas & tak menutup.
    # Saat menutup, bot membaca closing_reply APA ADANYA (verbatim) lalu 'langsung
    # tutup' (di Mode B koneksi WebSocket ditutup otomatis setelah salam penutup).
    "closing_enabled": "1",
    "closing_trigger_max_words": "5",
    "closing_triggers": (
        "terima kasih, terimakasih, makasih, makasih ya, terima kasih ya, "
        "terima kasih banyak, oke terima kasih, ok terima kasih, itu saja, itu aja, "
        "sekian, cukup sekian, tidak ada lagi, tidak ada lagi yang ditanyakan"
    ),
    "closing_hallucination_patterns": (
        "terima kasih telah menonton, terima kasih sudah menonton, "
        "terima kasih telah menyaksikan, terima kasih sudah menyaksikan, "
        "terima kasih telah mendengarkan, sampai jumpa di video selanjutnya, "
        "sampai jumpa di video berikutnya, jangan lupa like dan subscribe, "
        "like dan subscribe, jangan lupa subscribe"
    ),
    # --- STT prediktif / biasing + bias NLU (#5) ---
    # STT prediktif: membiasakan faster-whisper ke KOSAKATA DOMAIN supaya istilah
    # penting (NPWP, EFIN, SPT, dll.) & nama intent lebih akurat ditranskrip lewat
    # initial_prompt (teks domain) + hotwords (daftar istilah). Sumber istilah
    # digabung dari: stt_bias_terms (manual, koma) + kamus pelafalan vb_lexicon
    # (stt_bias_from_lexicon) + nama intent aktif (stt_bias_from_intents), lalu
    # di-dedup & dibatasi stt_bias_max_terms (jaga jendela prompt Whisper).
    # Bias NLU: bila KATA KUNCI tertentu muncul di transkrip, skor intent terkait
    # dinaikkan nlu_bias_boost. Pemetaan di nlu_bias_map, satu aturan per baris
    # (atau dipisah '|') dengan format 'kata kunci => Nama Intent'.
    "stt_bias_enabled": "1",
    "stt_bias_prompt": (
        "Percakapan layanan pelanggan perpajakan dalam Bahasa Indonesia. "
        "Istilah umum: NPWP, NIK, EFIN, SPT, PPh, PPN, DJP, KPP, KTP, "
        "e-Filing, e-Billing, e-Faktur."
    ),
    "stt_bias_terms": "",
    "stt_bias_from_lexicon": "1",
    "stt_bias_from_intents": "1",
    "stt_bias_max_terms": "64",
    "nlu_bias_enabled": "1",
    "nlu_bias_boost": "0.08",
    "nlu_bias_map": (
        "npwp => Cek Status NPWP\n"
        "status npwp => Cek Status NPWP\n"
        "jam buka => Jam Operasional\n"
        "jam operasional => Jam Operasional"
    ),
    "filler_enabled": "1",
    "filler_texts": (
        "Baik, mohon tunggu sebentar ya.|Baik, saya periksa dulu.|"
        "Mohon tunggu sebentar, ya."
    ),
    # --- penyingkat jawaban intent (2b) ---
    "intent_shorten_enabled": "1",
    "intent_shorten_min_chars": "160",
    "intent_shorten_system": (
        "Anda meringkas jawaban call center untuk dibacakan sebagai suara dalam "
        "Bahasa Indonesia. Persingkat menjadi 1-2 kalimat lisan yang sopan dan "
        "langsung ke inti, TANPA mengubah, menambah, atau menghilangkan fakta, "
        "angka, nominal, syarat, nama, atau langkah penting. Buang hanya kata "
        "berlebih dan pengulangan. Tanpa markdown, tanpa emoji. Bila teks sudah "
        "ringkas, kembalikan apa adanya."
    ),
    # --- mode suara natural (#4a) ---
    # tts_engine: 'piper' (default, ringan/cepat) | 'mms' (facebook/mms-tts-ind,
    # native Bahasa Indonesia, lebih natural). MMS butuh transformers+torch dan
    # unduhan model sekali dari HuggingFace; setelah itu jalan penuh lokal.
    "tts_engine": "piper",
    "mms_model": "facebook/mms-tts-ind",
    # --- cache TTS (latency) ---
    # Simpan hasil sintesis frasa berulang (salam/konfirmasi/penjaga diam/filler)
    # di memori proses supaya tak disintesis ulang -> hemat ~3 dtk/giliran.
    "tts_cache_enabled": "1",
    "tts_cache_max": "64",
    # --- resample OUTPUT TTS ke 8 kHz (Poin 2A, sisi keluaran) ---
    # 0 = mati (audio keluar byte-identik dg sebelumnya). Mis. 8000 -> semua audio
    # jawaban di-resample ke 8 kHz (kualitas kanal telepon/Avaya). Lihat voicebot.tts.
    "tts_target_sample_rate": "0",
    # --- band-limit 8 kHz INPUT STT (Poin 2A, sisi masukan) ---
    # 1 = turunkan audio masuk ke 8 kHz (lalu naik lagi) sebelum STT untuk menguji
    # ketahanan transkripsi pada kualitas telephony. Default 0 = mati.
    "stt_telephony_band": "0",
    # --- sentimen & valensi ringan (Poin 3.3) ---
    # Bila aktif, transkrip dianalisis leksikon ringan (voicebot.sentiment) untuk
    # menyesuaikan CARA menjawab: empati singkat + menawarkan agen lebih cepat saat
    # penelepon terdengar FRUSTRASI. Default MATI -> tanpa efek apa pun.
    #   sentiment_pos_words / sentiment_neg_words : PERLUAS (bukan ganti) leksikon
    #     default, daftar kata dipisah koma.
    #   sentiment_valence_cut     : ambang |valensi| utk melabeli neg/pos (0..1).
    #   sentiment_frustrated_min  : kekuatan sinyal minimal agar 'neg' = FRUSTRASI.
    #   sentiment_empathy_prefix  : kalimat empati yang diawalkan pada jawaban biasa.
    #   sentiment_empathy_enabled : 1 = pakai prefix empati saat frustrasi.
    #   sentiment_handoff_neg_streak : jumlah giliran frustrasi beruntun -> tawar agen.
    "sentiment_enabled": "0",
    "sentiment_pos_words": "",
    "sentiment_neg_words": "",
    "sentiment_valence_cut": "0.34",
    "sentiment_frustrated_min": "1.5",
    "sentiment_empathy_enabled": "1",
    "sentiment_empathy_prefix": "Mohon maaf atas ketidaknyamanannya.",
    "sentiment_handoff_neg_streak": "2",
    # --- pra-hasil jawaban / pregen (Poin 3.2) ---
    # Bila aktif, endpoint warmup menghangatkan cache shorten + TTS untuk frasa yang
    # sering dipakai (salam/penutup/filler/konfirmasi/jawaban intent) supaya giliran
    # awal tidak menanggung waktu shorten/TTS. Default MATI ('0').
    "pregen_enabled": "0",
    # --- streaming TTS per-kalimat (Poin 3.1, Mode B) ---
    # Bila aktif, jawaban di Mode B dibacakan PER-KALIMAT: tiap kalimat disintesis
    # lalu dikirim berurutan sehingga suara PERTAMA terdengar lebih cepat (TTFA
    # turun) -- kalimat berikutnya disintesis selagi kalimat sebelumnya diputar.
    # Default MATI ('0') -> jawaban disintesis penuh lalu dikirim sekali (perilaku
    # lama). tts_stream_min_chars: jawaban lebih pendek dari ini dikirim UTUH.
    "tts_stream_sentences": "0",
    "tts_stream_min_chars": "80",
    # --- penyambung instan / instant connector (Poin 3a, Mode B) ---
    # Bila aktif, BEGITU ucapan penelepon selesai (endpointing) bot langsung
    # memutar frasa penyambung singkat (mis. \"Baik, Kak.\") SELAGI engine masih
    # memproses STT/NLU/RAG/TTS di latar. Ini menutup jeda \"memproses\" sehingga
    # latensi TERASA jauh lebih kecil; jawaban lengkap menyusul otomatis setelah
    # frasa penyambung selesai (audio dijaga tak tumpang tindih via audio_lock).
    # Frasa dirotasi bergiliran dari daftar (dipisah '|') agar tak monoton. Frasa
    # sengaja NETRAL karena transkrip belum tersedia saat penyambung diputar
    # (empati untuk keluhan tetap muncul di isi jawaban). Default MATI ('0') ->
    # perilaku lama (tanpa penyambung). Berlaku untuk sesi Mode B berikutnya.
    "stream_connector_enabled": "0",
    "stream_connector_texts": (
        "Baik, Kak.|Baik, saya bantu ya, Kak.|Baik Kak, terkait hal tersebut."
    ),
    # --- streaming Mode B & barge-in (#4b) — DAPAT DIATUR DARI UI KONFIGURASI ---
    # Semua ambang di bawah bisa diubah dari panel \"Streaming (Mode B) & barge-in\"
    # di halaman /voicebot tanpa menyentuh kode/ENV. Bila sebuah nilai dikosongkan,
    # stream.py akan fallback ke ENV lama lalu default kode. Berlaku untuk sesi
    # streaming BERIKUTNYA (buka ulang percakapan Mode B setelah menyimpan).
    "stream_gate_rms": "0.012",        # gerbang noise sisi-browser (RMS 0..1); naikkan utk lebih ketat
    "stream_gate_hangover_ms": "600",  # tahan gerbang browser setelah ada suara (ms)
    "stream_rms": "600",               # ambang energi VAD server (fallback non-webrtcvad)
    "stream_vad_aggr": "3",            # agresivitas webrtcvad 0..3 (3 = paling tegas)
    "stream_silence_ms": "700",        # hening penanda akhir ucapan (ms)
    "stream_min_speech_ms": "350",     # durasi minimum agar dianggap ucapan (ms)
    "stream_preroll_ms": "300",        # pra-roll audio sebelum trigger (ms)
    "stream_bargein": "1",             # izinkan barge-in via mic saat bot bicara
    "stream_bargein_min_ms": "500",    # durasi bicara berkelanjutan utk konfirmasi barge-in (ms)
    # --- barge-in tahan-noise via konfirmasi STT (#3b) — DAPAT DIATUR DARI UI ---
    # Bila '1', energi bicara TIDAK langsung memotong bot. Bot terus bicara (sambil
    # di-duck) dan ucapan penyela direkam; pemotongan audio bot hanya dilakukan
    # SETELAH STT MENGONFIRMASI ada ucapan asli (ada transkrip). Efeknya: noise,
    # gema loudspeaker, ketikan, atau blip yang TIDAK menjadi transkrip tidak akan
    # pernah menghentikan bot di tengah kalimat. Default '0' = perilaku lama
    # (potong berbasis energi berkelanjutan >= stream_bargein_min_ms). Berlaku utk
    # sesi Mode B berikutnya. Cocok dipadukan dg speaking_rms/onset/voiced_ratio.
    "stream_bargein_confirm_stt": "0",
    "stream_speaking_rms": "900",      # ambang energi saat bot bicara (anti-gema loudspeaker)
    "stream_ducking": "1",             # kecilkan volume bot saat memverifikasi kandidat suara user
    "stream_duck_gain": "0.2",         # level volume bot saat di-duck (0..1)
    # --- noise vs speech / anti-noise adaptif (#6) — DAPAT DIATUR DARI UI ---
    # Membedakan SUARA ASLI dari NOISE lingkungan supaya bot tidak menjawab noise,
    # bunyi sesaat (klik/ketikan/pintu), atau dengungan steady. Tiga lapis:
    #   1) Lantai noise adaptif (stream_noise_adapt): server memantau energi ambient
    #      saat senyap (EMA) lalu menaikkan ambang deteksi jadi noise_floor * snr_ratio,
    #      sehingga menyesuaikan lingkungan tiap penelepon (sepi vs berisik). Ambang
    #      efektif = max(ambang energi biasa, noise_floor * snr_ratio) -> hanya
    #      MENGETATKAN, tak pernah lebih longgar dari stream_rms/stream_speaking_rms.
    #   2) Frame onset (stream_onset_frames): butuh N frame bersuara BERURUTAN
    #      (30ms/frame) sebelum memicu awal bicara -> buang klik/pop/ketikan sesaat.
    #   3) Rasio frame bersuara (stream_voiced_ratio_min): ucapan hanya diterima bila
    #      minimal sekian bagian frame-nya benar-benar bersuara -> buang segmen yang
    #      didominasi noise. Set 0 untuk menonaktifkan cek rasio. Semua nilai bisa
    #      dikosongkan (fallback ENV lalu default kode).
    "stream_noise_adapt": "1",
    "stream_snr_ratio": "1.8",
    "stream_noise_floor_init": "150",
    "stream_onset_frames": "3",
    "stream_voiced_ratio_min": "0.35",
    # --- penjaga diam / silence watchdog (#3) — DAPAT DIATUR DARI UI ---
    # Khusus Mode B (streaming). Bila penelepon diam: setelah stream_idle_prompt_ms
    # tanpa suara, bot menyapa (stream_idle_prompt_text, mis. \"masih terhubung?\").
    # Bila diam berlanjut stream_idle_end_ms lagi tanpa respons, sesi diakhiri
    # otomatis (bot membaca stream_idle_end_text lalu menutup koneksi). Timer diam
    # hanya berjalan saat bot TIDAK bicara/memproses. {sal}=sapaan.
    "stream_idle_enabled": "1",
    "stream_idle_prompt_ms": "8000",
    "stream_idle_prompt_text": "Halo, apakah masih terhubung, {sal}?",
    "stream_idle_end_ms": "10000",
    "stream_idle_end_text": (
        "Baik, karena belum ada respons, panggilan saya akhiri dulu ya. "
        "Terima kasih sudah menghubungi kami."
    ),
}

_INIT_DONE = set()


def connect():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass
    return conn


def _migrate(conn):
    """Migrasi ringan idempoten untuk DB lama (tambah kolom baru bila belum ada)."""
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(vb_intents)").fetchall()]
        if "confirm_label" not in cols:
            conn.execute("ALTER TABLE vb_intents ADD COLUMN confirm_label TEXT")
            conn.commit()
    except Exception:
        pass


def init_db(conn, force=False):
    key = None
    try:
        r = conn.execute("PRAGMA database_list").fetchone()
        key = r[2] if r else None
    except Exception:
        key = None
    if not force and key and key in _INIT_DONE:
        return conn
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS vb_settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS vb_intents (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL,
            phrases       TEXT,
            response      TEXT,
            confirm_label TEXT,
            aktif         INTEGER DEFAULT 1,
            created_at    TEXT DEFAULT (datetime('now')),
            updated_at    TEXT DEFAULT (datetime('now'))
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_vb_intent_name ON vb_intents(name);
        CREATE TABLE IF NOT EXISTS vb_lexicon (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern     TEXT NOT NULL,
            replacement TEXT,
            mode        TEXT DEFAULT 'eja',
            enabled     INTEGER DEFAULT 1,
            notes       TEXT,
            created_at  TEXT DEFAULT (datetime('now')),
            updated_at  TEXT DEFAULT (datetime('now'))
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_vb_lexicon_pat ON vb_lexicon(pattern);
        CREATE TABLE IF NOT EXISTS vb_turns (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            id_trace   TEXT,
            user_text  TEXT,
            intent     TEXT,
            confidence REAL,
            sumber     TEXT,
            bot_text   TEXT,
            handoff    INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_vb_turns_sess ON vb_turns(session_id);
        """
    )
    conn.commit()
    _migrate(conn)
    for k, v in DEFAULT_SETTINGS.items():
        try:
            conn.execute(
                "INSERT OR IGNORE INTO vb_settings(key, value) VALUES (?, ?)",
                (k, v),
            )
        except Exception:
            pass
    conn.commit()
    if key:
        _INIT_DONE.add(key)
    try:
        seed_intents(conn)
    except Exception:
        pass
    try:
        seed_lexicon(conn)
    except Exception:
        pass
    return conn


# ------------------------------------------------------------------ settings
def get_settings(conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        out = dict(DEFAULT_SETTINGS)
        for r in conn.execute("SELECT key, value FROM vb_settings").fetchall():
            out[r["key"]] = r["value"]
        return out
    finally:
        if own:
            conn.close()


def get_setting(key, default=None, conn=None):
    s = get_settings(conn)
    return s.get(key, default if default is not None else DEFAULT_SETTINGS.get(key))


def set_settings(data, conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        n = 0
        for k, v in (data or {}).items():
            conn.execute(
                "INSERT INTO vb_settings(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(k), "" if v is None else str(v)),
            )
            n += 1
        conn.commit()
        return {"saved": n}
    finally:
        if own:
            conn.close()


# ------------------------------------------------------------------ backup/restore
def export_config(conn=None):
    """Snapshot lengkap konfigurasi (settings + intents + lexicon), JSON-able.

    Dipakai fitur backup/restore. exported_at diisi waktu server (SQLite
    datetime('now')). Semua nilai bertipe dasar sehingga aman di-json.dumps.
    """
    own = conn is None
    conn = conn or init_db(connect())
    try:
        try:
            ts = conn.execute("SELECT datetime('now')").fetchone()[0]
        except Exception:
            ts = None
        settings = {}
        for r in conn.execute("SELECT key, value FROM vb_settings").fetchall():
            settings[r["key"]] = r["value"]
        intents = []
        for r in conn.execute(
            "SELECT name, phrases, response, confirm_label, aktif "
            "FROM vb_intents ORDER BY name"
        ).fetchall():
            d = dict(r)
            d["phrases_list"] = _phr_list(d.get("phrases"))
            intents.append(d)
        lexicon = []
        for r in conn.execute(
            "SELECT pattern, replacement, mode, enabled, notes "
            "FROM vb_lexicon ORDER BY pattern"
        ).fetchall():
            lexicon.append(dict(r))
        return {
            "type": "camerad-voicebot-config",
            "version": 1,
            "exported_at": ts,
            "settings": settings,
            "intents": intents,
            "lexicon": lexicon,
        }
    finally:
        if own:
            conn.close()


def import_config(data, mode="merge", conn=None):
    """Terapkan snapshot export_config() kembali ke DB. Kembalikan jumlah entri.

    mode:
      'merge'   -> settings ditimpa/ditambah; intents & lexicon di-upsert
                   (baris dengan nama/pattern sama diperbarui, sisanya ditambah).
      'replace' -> intents & lexicon DIKOSONGKAN dulu lalu diisi dari data;
                   settings tetap ditimpa/ditambah (tidak dihapus).
    Fail-soft per entri (satu entri rusak tak menggagalkan seluruh impor).
    """
    if not isinstance(data, dict):
        raise ValueError("data konfigurasi tidak valid (harus objek JSON).")
    mode = (str(mode or "merge").strip().lower())
    if mode not in ("merge", "replace"):
        mode = "merge"
    own = conn is None
    conn = conn or init_db(connect())
    try:
        counts = {"settings": 0, "intents": 0, "lexicon": 0}
        settings = data.get("settings") or {}
        if isinstance(settings, dict) and settings:
            set_settings(settings, conn=conn)
            counts["settings"] = len(settings)
        if mode == "replace":