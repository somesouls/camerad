# -*- coding: utf-8 -*-
"""eval_sampler.py — Kumpulkan sampel pertanyaan uji untuk evaluasi RAG.

Dua sumber (sesuai kesepakatan peluncuran):
  - LIVECHAT (avaya_db.awe_conversations): pertanyaan customer + GOLD = balasan
    agen. Dipakai menilai benar/salah/halusinasi/abstain.
  - CHATBOT (analytics_db.interactions): pertanyaan asli user ke chatbot
    Dialogflow. Tanpa gold (coverage-only) -> fokus grounded/abstain.

Sampling: stratified (round-robin antar strata) + dedup near-duplicate agar
sampel beragam dan tidak menumpuk di 1-2 topik populer.

TAHAP 1 (perbaikan validitas eval):
  - SARING utterance yang bukan pertanyaan yang bisa berdiri sendiri: salam,
    penyebutan nama ("saya X", "GIAN ABDUL HAPIDZ"), afirmasi pendek ("iya",
    "belum pernah"), dan fragmen lanjutan yang bergantung konteks percakapan
    sebelumnya. Lihat _good_question().
  - BERSIHKAN gold dari boilerplate sapaan agen (salam, perkenalan, tanya nama,
    minta menunggu) sehingga gold berisi jawaban substantif; sampel yang gold-nya
    tinggal basa-basi otomatis di-drop. Lihat _clean_gold().

TAHAP 1.5 (validitas lanjutan):
  - DROP sampel yang gold-nya pada dasarnya AKSI-AGEN yang tak bisa direplikasi
    chatbot pengetahuan: verifikasi identitas / pengumpulan data pribadi
    (NIK/NPWP/nama/alamat/email/HP), pengalihan ke AR/KPP, atau pernyataan
    "tidak dapat diproses via chat" TANPA muatan pengetahuan/prosedur nyata.
    Sampel seperti ini membuat mesin selalu dinilai "salah" secara tidak adil.
    Lihat _gold_answerable().
"""
import re
import json
import random

import eval_db
import avaya_db as avdb
import analytics_db as adb

_STOP = set("yang dan di ke dari untuk pada dengan atau ini itu ada apa bagaimana "
            "gimana kenapa mengapa min admin kak pak bu mohon tolong ya nya saya aku "
            "kami kita mau ingin bisa tidak gak ga nggak sudah belum juga kalau jika "
            "saja lagi kok dong sih halo hai cara adalah akan the a an is to of for".split())

_GREET = re.compile(r"^(halo|hai|hi|hallo|assalamu|selamat\s+(pagi|siang|sore|malam)|"
                    r"pagi|siang|sore|malam|permisi|maaf|terima\s+kasih|makasih|"
                    r"ok|oke|ya|iya|test|tes)\b", re.I)

# Awalan yang menandai utterance BUKAN pertanyaan mandiri (nama diri, afirmasi,
# atau fragmen lanjutan yang bersandar pada giliran percakapan sebelumnya).
_NONQ_PREFIX = re.compile(
    r"^(saya|aku|nama\s+saya|perkenalkan|dengan|iya|ya|betul|benar|baik|oke|ok|"
    r"sudah|belum|makasih|terima\s+kasih|dan|lalu|terus|trus|kemudian|sedangkan|"
    r"tapi|namun|berarti|jadi|oh|my\s+name|i\s+am|i'm)\b", re.I)

# Sinyal bahwa utterance kemungkinan pertanyaan/permintaan.
_QWORD = re.compile(
    r"(\?|\bapa(kah)?\b|\bbagaimana\b|\bbgmn\b|\bgimana\b|\bgmn\b|\bkenapa\b|"
    r"\bmengapa\b|\bberapa\b|\bkapan\b|\bdi\s?mana\b|\bdimana\b|\bbisakah\b|"
    r"\bbolehkah\b|\bapakah\b|\bmohon\b|\btolong\b|\bcara\b|\bhow\b|\bwhat\b|"
    r"\bwhy\b|\bcan\s+i\b)", re.I)

# Kata kunci domain pajak: sinyal isi substantif walau tanpa kata tanya eksplisit.
_TAXKW = re.compile(
    r"(pajak|npwp|nik|coretax|core\s?tax|spt|ppn|pph|faktur|bupot|efin|djp|"
    r"sertifikat|billing|restitusi|\bpkp\b|\bpbk\b|skpkb|\bstp\b|nitku|suket|"
    r"\bpp\s?55\b|\bpmk\b|\bper[- ]|lapor|bayar|angsur|kredit|retur|aktivasi|"
    r"unduh|download|daftar|pemadanan|nonaktif|dokumen|billing|tax|register)", re.I)

# TAHAP 1.5: penanda gold = AKSI-AGEN (verifikasi/pengumpulan data/pengalihan)
# yang tak bisa dilakukan chatbot pengetahuan.
_ACTION_MARK = [re.compile(p, re.I) for p in (
    r"(sebutkan|lengkapi|melengkapi|mengisi|isi)\s+data\s+berikut",
    r"data\s+sebagai\s+berikut",
    r"(sebutkan|lengkapi)\b.*\bnpwp\b",
    r"\bnpwp\s*\(",
    r"alamat\s+(terdaftar|lengkap\s+terdaftar|tempat\s+tinggal\s+terdaftar)",
    r"email\s+terdaftar",
    r"(nomor|no\.?)\s*(telepon|hp|handphone)\s*/?\s*(hp\s*)?terdaftar",
    r"data\s+by\s+system",
    r"validasi\s+data",
    r"menghubungi\s+ar\b",
    r"hubungi\s+ar\b",
    r"\bar\s+terkait\b",
    r"\bkpp\s+(terdaftar|terdekat)\b",
    r"menghubungi\s+kpp\b",
    r"tidak\s+dapat\s+(kami\s+)?(proses|lanjutkan|diproses|dilanjutkan)",
    r"tidak\s+(memiliki|mempunyai)\s+(kewenangan|akses|wewenang)",
    r"we\s+do\s+not\s+have\s+(the\s+)?(access|authority)",
    r"kindly\s+contact\s+(your\s+)?(registered\s+)?tax\s+office",
)]

# Penanda gold memuat PENGETAHUAN/PROSEDUR yang bisa direplikasi chatbot.
_KNOW_MARK = [re.compile(p, re.I) for p in (
    r"\bpasal\b",
    r"\bpmk\b",
    r"\bper[- ]?\d",
    r"\bpp\s?\d",
    r"\bundang-undang\b|\buu\b",
    r"\bayat\b",
    r"portal\s+saya|profil\s+saya",
    r"\bmenu\b",
    r"\blangkah\b",
    r"silakan\s+(pada|akses|buka|masuk|login|klik|pilih|gunakan|menggunakan)",
    r"melalui\s+coretax|pada\s+coretax|di\s+coretax",
    r"tidak\s+perlu\s+efin",
    r"nik\s*=\s*npwp|nik\s+sebagai\s+npwp|nik\s+menjadi\s+npwp",
    r"\d{2}[.:]\d{2}",
    r"senin\s+(s\.?d\.?|sampai)\s+jumat",
)]


def _norm(s):
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^\w\s]", "", s)
    return s


def _sig(s):
    """Tanda-tangan token-set untuk deteksi near-duplicate."""
    toks = [w for w in _norm(s).split() if len(w) >= 3 and w not in _STOP]
    return " ".join(sorted(set(toks))[:10])


def _is_customer(role):
    return (role or "").strip().lower() in avdb._CUST_ROLES


def _is_greeting(t):
    t = (t or "").strip()
    if len(t) < 8:
        return True
    return bool(_GREET.match(t))


def _is_name_like(t):
    """True bila utterance hanya berupa nama/sapaan diri (1-4 kata alfabet,
    tanpa angka, tanpa kata tanya/kata kunci pajak). Mis. "GIAN ABDUL HAPIDZ".
    """
    words = (t or "").split()
    if not (1 <= len(words) <= 4):
        return False
    if any(ch.isdigit() for ch in t):
        return False
    if _QWORD.search(t) or _TAXKW.search(t):
        return False
    return all(re.fullmatch(r"[A-Za-z\u00c0-\u017f.'-]+", w) for w in words)


def _good_question(t):
    """True bila utterance layak jadi sampel uji: pertanyaan/keluhan yang bisa
    berdiri sendiri. Menolak salam, penyebutan nama, afirmasi pendek, dan
    fragmen lanjutan yang bergantung konteks percakapan sebelumnya.
    """
    t = (t or "").strip()
    if len(t) < 12:
        return False
    low = t.lower()
    has_signal = bool(_QWORD.search(low) or _TAXKW.search(low))
    meaningful = [w for w in _norm(t).split() if len(w) >= 3 and w not in _STOP]
    if not has_signal:
        # Tanpa sinyal apa pun: tolak salam/nama/afirmasi/fragmen lanjutan.
        if _is_greeting(t) or _NONQ_PREFIX.match(low) or _is_name_like(t):
            return False
        # Butuh isi cukup substansial agar tidak sekadar potongan kalimat.
        return len(meaningful) >= 4 and len(t) >= 25
    # Ada sinyal pertanyaan/kata kunci pajak.
    return len(meaningful) >= 2


# Boilerplate sapaan agen yang harus dibuang dari gold agar tersisa jawaban isi.
_BOILER = [re.compile(p, re.I) for p in (
    r"terima kasih telah menunggu",
    r"saat ini anda telah terhubung dengan live agent kami",
    r"mohon menunggu respon dari live agent kami",
    r"terima kasih telah bersedia menunggu",
    r"mohon menunggu sebentar[, ]*kami pastikan terlebih dahulu ketentuannya",
    r"selamat\s+(pagi|siang|sore|malam)",
    r"good\s+(morning|afternoon|evening|day)",
    r"perkenalkan[, ]*saya\s+\w+",
    r"saya\s+\w+[, ]*agen[t]?\s+(live\s*chat\s+)?kring\s*pajak\s*1500200",
    r"saya\s+\w+\s+dari\s+kring\s*pajak\s*1500200",
    r"i\s+am\s+\w+[, ]*a?\s*live\s*chat\s+agent\s+from\s+kring\s*pajak\s*1500200",
    r"agen[t]?\s+live\s*chat\s+kring\s*pajak\s*1500200",
    r"(sebelumnya[, ]*)?dengan\s+(bapak\s*/\s*ibu|bapak|ibu)\s+siapa\s+(saya|kami)\s+terhubung\s*\??",
    r"mohon\s+maaf\s+sebelumnya[, ]*",
    r"may\s+i\s+have\s+your\s+name\s*\??",
    r"ada\s+yang\s+(bisa|dapat)\s+(saya|kami)\s+(bantu|dibantu)\s*\??",
    r"ada\s+yang\s+(bisa|dapat)\s+dibantu\s*\??",
    r"kring\s*pajak\s*1500200",
)]


def _clean_gold(gold):
    """Buang boilerplate sapaan agen (salam, perkenalan, tanya nama, minta
    menunggu) agar gold berisi jawaban substantif. Kembalikan '' bila tak
    tersisa isi berarti -> pemanggil sebaiknya men-drop sampel seperti itu.
    """
    g = " " + (gold or "").strip() + " "
    for rx in _BOILER:
        g = rx.sub(" ", g)
    # Buang satu pembuka konfirmasi nama di awal: "Baik, Ibu Adel."
    g = re.sub(r"^\s*baik[,.\s]+(bapak\s*/\s*ibu|bapak|ibu|pak|bu)\s+\w+[,.\s]+",
               " ", g, flags=re.I)
    g = re.sub(r"\s+", " ", g).strip(" ,.-")
    return g


def _gold_answerable(gold):
    """TAHAP 1.5: True bila gold memuat jawaban pengetahuan yang WAJAR
    direplikasi chatbot. False bila gold pada dasarnya aksi-agen
    (verifikasi/pengumpulan data pribadi atau pengalihan ke AR/KPP) tanpa
    muatan pengetahuan -> sampel tak adil untuk menilai chatbot.
    """
    g = gold or ""
    if not g.strip():
        return False
    action = sum(1 for rx in _ACTION_MARK if rx.search(g))
    know = sum(1 for rx in _KNOW_MARK if rx.search(g))
    if action >= 1 and know == 0:
        return False
    if action >= 3 and know <= 1:
        return False
    return True


def _extract_qa(transkrip):
    """Dari list [{role,text}] -> (pertanyaan_customer, gold_agen) atau None.

    - pertanyaan: giliran customer PERTAMA yang lolos _good_question (melewati
      salam/penyebutan nama pembuka).
    - gold: gabungan balasan agen, dibersihkan dari boilerplate sapaan. Sampel
      di-drop bila tak ada pertanyaan valid, gold isi < 40 karakter, atau gold
      pada dasarnya aksi-agen yang tak bisa direplikasi chatbot (_gold_answerable).
    """
    if not isinstance(transkrip, list):
        return None
    cust_q = None
    agent_parts = []
    for seg in transkrip:
        if not isinstance(seg, dict):
            continue
        role = seg.get("role", "")
        text = (seg.get("text", "") or "").strip()
        if not text:
            continue
        if _is_customer(role):
            if cust_q is None and _good_question(text):
                cust_q = text
        elif avdb._is_agent(role, text):
            agent_parts.append(text)
    gold = _clean_gold(" ".join(agent_parts))
    if not cust_q or not gold or len(gold) < 40:
        return None
    if not _gold_answerable(gold):
        return None
    return cust_q, gold


def _stratified(buckets, n, seed=42):
    """Round-robin antar strata (buckets: {label: [item...]}) sampai n."""
    rnd = random.Random(seed)
    order = list(buckets.keys())
    rnd.shuffle(order)
    pools = {}
    for k in order:
        v = list(buckets[k])
        rnd.shuffle(v)
        pools[k] = v
    out = []
    while len(out) < n:
        progressed = False
        for k in order:
            if pools[k]:
                out.append((k, pools[k].pop()))
                progressed = True
                if len(out) >= n:
                    break
        if not progressed:
            break
    return out


def collect_livechat(n=300, seed=42):
    econn = eval_db.init_db(eval_db.connect())
    try:
        c = avdb.init_db(avdb.connect())
    except Exception as e:
        econn.close()
        return {"ok": False, "error": "avaya.db tak terbaca: %s" % e}
    try:
        rows = c.execute(
            "SELECT sid, mapped_intent, jenis_layanan, topik, transkrip_json "
            "FROM awe_conversations WHERE transkrip_json IS NOT NULL"
        ).fetchall()
    except Exception:
        rows = []
    finally:
        try:
            c.close()
        except Exception:
            pass
    seen = set()
    buckets = {}
    for r in rows:
        d = dict(r)
        try:
            tx = json.loads(d.get("transkrip_json") or "[]")
        except Exception:
            continue
        qa = _extract_qa(tx)
        if not qa:
            continue
        q, gold = qa
        sig = _sig(q)
        if not sig or sig in seen:
            continue
        seen.add(sig)
        label = (d.get("jenis_layanan") or d.get("mapped_intent") or d.get("topik") or "Lainnya")
        buckets.setdefault(str(label), []).append(
            {"q": q, "gold": gold, "ref": d.get("sid") or "", "label": str(label)})
    picked = _stratified(buckets, n, seed)
    added = 0
    for label, it in picked:
        eval_db.upsert_sample(econn, "livechat", it["q"], gold=it["gold"],
                              label=it["label"], sumber_ref=it["ref"], holdout=1)
        added += 1
    econn.commit()
    counts = eval_db.sample_counts(econn)
    econn.close()
    return {"ok": True, "jenis": "livechat", "kandidat": len(seen),
            "strata": len(buckets), "ditambah": added, "counts": counts}


def collect_chatbot(n=200, seed=42):
    econn = eval_db.init_db(eval_db.connect())
    try:
        c = adb.init_db(adb.connect())
    except Exception as e:
        econn.close()
        return {"ok": False, "error": "analytics.db tak terbaca: %s" % e}
    try:
        rows = c.execute(
            "SELECT user_phrase, intent_name, is_fallback FROM interactions "
            "WHERE is_system=0 AND length(COALESCE(user_phrase,''))>=8"
        ).fetchall()
    except Exception:
        rows = []
    finally:
        try:
            c.close()
        except Exception:
            pass
    seen = set()
    buckets = {}
    for r in rows:
        d = dict(r)
        q = (d.get("user_phrase") or "").strip()
        if not _good_question(q):
            continue
        sig = _sig(q)
        if not sig or sig in seen:
            continue
        seen.add(sig)
        if d.get("is_fallback"):
            label = "(fallback/tak dikenali)"
        else:
            label = d.get("intent_name") or "(lainnya)"
        buckets.setdefault(str(label), []).append({"q": q, "label": str(label)})
    picked = _stratified(buckets, n, seed)
    added = 0
    for label, it in picked:
        eval_db.upsert_sample(econn, "chatbot", it["q"], gold=None,
                              label=it["label"], sumber_ref="", holdout=1)
        added += 1
    econn.commit()
    counts = eval_db.sample_counts(econn)
    econn.close()
    return {"ok": True, "jenis": "chatbot", "kandidat": len(seen),
            "strata": len(buckets), "ditambah": added, "counts": counts}


def collect_all(n_live=300, n_chat=200, seed=42):
    a = collect_livechat(n_live, seed=seed)
    b = collect_chatbot(n_chat, seed=seed)
    return {"ok": bool(a.get("ok") or b.get("ok")), "livechat": a, "chatbot": b}
