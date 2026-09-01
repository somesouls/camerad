# -*- coding: utf-8 -*-
"""avaya/phone_llm.py - Lapisan LLM: rapikan + ringkas + analisis transkrip Telepon.

Mengubah transkrip STT yang masih kasar (audio telepon 8 kHz) menjadi keluaran
terstruktur: dialog 2 penutur yang rapi, ringkasan, topik, sentimen, emosi,
resolusi, dan entitas. Dipakai probe_llm.py (uji terminal) dan jalur web.

Backend LLM = common.llm_client (provider via .env: openai/azure/gemini/local).
Untuk data pajak yang sensitif, set LLM_PROVIDER=local (vLLM Qwen) agar
transkrip TIDAK keluar dari mesin.

Glosarium koreksi STT diambil dari Glosarium Pajak internal via phone_glossary
(istilah status 'aktif'); bila tak tersedia, pakai fallback ringkas _GLOSARIUM.

Taksonomi diselaraskan dengan pipeline Chat (avaya/pipeline.py):
  sentimen: Positif | Negatif | Netral
  emosi   : Senang/Bahagia | Marah/Frustrasi | Sedih/Kecewa | Takut/Cemas |
            Terkejut | Jijik/Bosan | Netral/Datar
  resolusi: Terselesaikan | Belum Terselesaikan
"""
import json
import os

try:
    from .phone_glossary import glossary_block as _glossary_block
except Exception:
    try:
        from phone_glossary import glossary_block as _glossary_block
    except Exception:
        _glossary_block = None

_NL = chr(10)

SENTIMEN_VALS = ("Positif", "Negatif", "Netral")
EMOSI_VALS = ("Senang/Bahagia", "Marah/Frustrasi", "Sedih/Kecewa",
              "Takut/Cemas", "Terkejut", "Jijik/Bosan", "Netral/Datar")
RESOLUSI_VALS = ("Terselesaikan", "Belum Terselesaikan")

_SYSTEM = (
    "Anda asisten analis QA untuk rekaman percakapan layanan pelanggan "
    "Kring Pajak (Direktorat Jenderal Pajak/DJP). Transkrip berasal dari "
    "Speech-to-Text audio telepon 8 kHz sehingga sering salah dengar, "
    "terutama nama orang dan deret angka, dan kadang mengulang kata. Tugas "
    "Anda merapikan dan menganalisis: perbaiki salah dengar yang jelas dari "
    "konteks TAPI jangan mengarang fakta. Bila nama/nomor tidak yakin, "
    "pertahankan lalu beri tanda [?]. Seluruh keluaran dalam Bahasa Indonesia "
    "dan HANYA berupa satu objek JSON valid tanpa penjelasan atau pembungkus "
    "markdown."
)

_PENUTUR = _NL.join([
    "PENTING - penentuan penutur: transkrip TIDAK memuat penanda pembicara "
    "(hasil STT polos), jadi Anda harus menyimpulkan sendiri tiap giliran "
    "dengan patokan berikut:",
    "- AGEN = petugas Kring Pajak. Ciri: membuka dengan salam seperti 'Kring "
    "Pajak, dengan <nama>' atau memperkenalkan diri 'dengan <nama>', menyapa "
    "'baik ibu/bapak', MEMANDU langkah, MENJELASKAN prosedur/aturan, MEMINTA "
    "data verifikasi, dan berbahasa formal-sopan.",
    "- PENELEPON = wajib pajak. Ciri: MENYAMPAIKAN keluhan/pertanyaan ('saya "
    "mau daftar NPWP', 'kok error', 'bagaimana caranya'), sering bingung, dan "
    "meminta bantuan.",
    "Nama yang disebut pada salam pembuka ('dengan <nama>') adalah nama AGEN, "
    "BUKAN penelepon. Jaga konsistensi peran: giliran bicara berselang-seling "
    "secara wajar, jangan menukar peran di tengah percakapan. Bila satu kalimat "
    "ambigu, pakai alur: yang menjelaskan solusi = Agen; yang punya masalah = "
    "Penelepon.",
])

_PENUTUR_KNOWN = _NL.join([
    "PENTING - penentuan penutur: transkrip berasal dari DUA KANAL AUDIO "
    "TERPISAH, jadi label penutur pada tiap segmen SUDAH DIKETAHUI dan ANDAL "
    "dari kanal (bukan tebakan). Gunakan label 'Agen'/'Penelepon' pada tiap "
    "segmen APA ADANYA dan JANGAN menukar peran. Segmen sudah diurutkan menurut "
    "waktu; susun 'dialog' mengikuti urutan waktu itu. Bila dua segmen waktunya "
    "berdekatan atau tumpang tindih, pertahankan keduanya sebagai giliran "
    "terpisah. Nama yang disebut pada salam pembuka tetap nama AGEN, bukan "
    "penelepon.",
])

_INSTRUKSI = _NL.join([
    "Kembalikan HANYA satu objek JSON dengan kunci persis berikut:",
    '- dialog: array objek {"penutur":"Agen" atau "Penelepon","teks":"..."} rekonstruksi giliran bicara.',
    "- ringkasan: 1-3 kalimat inti percakapan.",
    "- topik: label topik singkat.",
    "- jenis_layanan: salah satu 'Informasi','Permohonan layanan','Pengaduan','Tindak lanjut','Lainnya'.",
    "- sentimen: salah satu 'Positif','Negatif','Netral'.",
    "- emosi: salah satu 'Senang/Bahagia','Marah/Frustrasi','Sedih/Kecewa','Takut/Cemas','Terkejut','Jijik/Bosan','Netral/Datar'.",
    "- resolusi: salah satu 'Terselesaikan','Belum Terselesaikan'.",
    "- frustrasi: true atau false.",
    '- entitas: objek {"nama":[...],"nomor":[...],"lainnya":[...]}; pada "nama" JANGAN masukkan nama agen, hanya nama/pihak yang benar-benar disebut dalam percakapan.',
    "- poin_penting: array kalimat pendek.",
    "- catatan_kualitas: catatan singkat soal bagian transkrip yang diragukan.",
])

# Fallback ringkas bila Glosarium Pajak internal tak tersedia. Juga selalu
# disertakan untuk istilah kanal yang biasanya belum ada di glosarium DB.
_GLOSARIUM = _NL.join([
    "Catatan domain untuk membantu menafsirkan salah dengar STT (audio 8 kHz):",
    "- Kring Pajak = layanan call center DJP (1500200).",
    "- MELATI = Meja Layanan TI di DJP (helpdesk tiket kendala aplikasi); STT "
    "sering salah dengar menjadi 'melati', 'pengakti', atau 'pengaktifan'.",
    "- Istilah umum yang mungkin muncul: NPWP, NIK, EFIN, SPT, DJP Online, "
    "Coretax, e-Faktur, e-Bupot, kode billing, NTPN, tiket.",
    "Pakai untuk mengoreksi frasa yang mirip bunyi, tetapi jangan memaksakan "
    "koreksi bila konteks jelas menunjukkan makna lain.",
])


def _strip_json(s):
    """Ambil satu objek JSON dari keluaran LLM (buang markdown/teks lain)."""
    if not s:
        return ""
    t = s.strip()
    if t.startswith("```"):
        nl = t.find(_NL)
        t = t[nl + 1:] if nl >= 0 else t.lstrip("`")
        if t.endswith("```"):
            t = t[:-3]
        t = t.strip()
    i = t.find("{")
    j = t.rfind("}")
    if i >= 0 and j > i:
        return t[i:j + 1]
    return t


_FIX_SYSTEM = ("Anda memperbaiki JSON rusak. Keluarkan HANYA satu objek JSON "
               "valid tanpa penjelasan atau pembungkus markdown.")


def _fix_prompt(reply, err):
    """Prompt perbaikan: minta LLM mengubah keluaran rusak jadi JSON valid."""
    return _NL.join([
        "Objek JSON di bawah TIDAK valid (galat parser: " + (err or "") + ").",
        "Perbaiki menjadi SATU objek JSON valid dengan kunci yang sama.",
        "WAJIB escape setiap tanda kutip ganda di dalam nilai teks, dan JANGAN "
        "memakai baris baru mentah di dalam string.",
        "Keluarkan HANYA objek JSON, tanpa teks lain.",
        "",
        reply or "",
    ])


def _coerce(val, allowed, default):
    v = str(val or "").strip().lower()
    for a in allowed:
        if v == a.lower():
            return a
    return default


def _glossary_text():
    """Gabungan catatan domain statis + Glosarium Pajak internal (bila ada)."""
    parts = [_GLOSARIUM]
    if _glossary_block:
        try:
            gl = _glossary_block() or ""
        except Exception:
            gl = ""
        if gl:
            parts.append("")
            parts.append(gl)
    return _NL.join(parts)


def _build_user(text, segments):
    parts = ["Transkrip mentah STT:", (text or "(kosong)").strip(), ""]
    has_spk = False
    if segments:
        has_spk = any(isinstance(s, dict) and (s.get("penutur") or "").strip()
                      for s in segments)
        head = "Segmen berwaktu (detik)"
        if has_spk:
            head += " dengan penutur dari kanal audio terpisah"
        parts.append(head + ":")
        for s in segments[:80]:
            try:
                spk = (s.get("penutur") or "").strip()
                pre = (spk + ": ") if spk else ""
                parts.append("[%.1f-%.1f] %s%s" % (
                    float(s.get("start") or 0.0),
                    float(s.get("end") or 0.0),
                    pre,
                    (s.get("text") or "").strip()))
            except Exception:
                continue
        parts.append("")
    parts.append(_glossary_text())
    parts.append("")
    parts.append(_PENUTUR_KNOWN if has_spk else _PENUTUR)
    parts.append("")
    parts.append(_INSTRUKSI)
    return _NL.join(parts)


def analyze_transcript(text, segments=None, max_new_tokens=None, temperature=0.2):
    """Rapikan + analisis transkrip via LLM. Kembalikan dict aman-JSON.

    Bentuk: {ok, provider, model, analysis{...}, raw, error?}

    Ketangguhan JSON: bila keluaran pertama gagal di-parse (mis. tanda kutip di
    dalam teks tidak ter-escape), otomatis minta LLM sekali lagi untuk
    memperbaikinya menjadi JSON valid sebelum menyerah.
    """
    out = {"ok": False, "provider": (os.environ.get("LLM_PROVIDER") or "openai"),
           "model": "", "analysis": None, "raw": ""}
    if not max_new_tokens:
        try:
            max_new_tokens = int(os.environ.get("AWE_PHONE_LLM_MAXTOK") or 4000)
        except Exception:
            max_new_tokens = 4000
    if not (text or "").strip():
        out["error"] = "Transkrip kosong; tidak ada yang dianalisis."
        return out
    try:
        import common.llm_client as llm
    except Exception as e:
        out["error"] = "common.llm_client tak bisa diimpor: %r" % e
        return out
    try:
        llm.init_client()
    except Exception as e:
        out["error"] = ("LLM belum siap: %r. Set LLM_PROVIDER + kunci API di .env, "
                        "atau LLM_PROVIDER=local untuk vLLM lokal.") % e
        return out
    try:
        out["model"] = str(getattr(llm, "_model", "") or "")
    except Exception:
        pass
    try:
        reply = llm.chat([{"role": "user", "content": _build_user(text, segments)}],
                         system=_SYSTEM, max_new_tokens=max_new_tokens,
                         temperature=temperature)
    except Exception as e:
        out["error"] = "Panggilan LLM gagal: %r" % e
        return out
    out["raw"] = reply or ""
    data = None
    perr = ""
    try:
        data = json.loads(_strip_json(reply))
    except Exception as e:
        perr = "%r" % e
    if data is None:
        try:
            fixed = llm.chat([{"role": "user", "content": _fix_prompt(reply, perr)}],
                             system=_FIX_SYSTEM, max_new_tokens=max_new_tokens,
                             temperature=0.0)
            out["raw"] = fixed or out["raw"]
            data = json.loads(_strip_json(fixed))
        except Exception as e:
            out["error"] = ("Keluaran LLM bukan JSON valid: %s (perbaikan otomatis "
                            "gagal: %r)") % (perr, e)
            return out
    if not isinstance(data, dict):
        out["error"] = "Keluaran LLM bukan objek JSON."
        return out
    data["sentimen"] = _coerce(data.get("sentimen"), SENTIMEN_VALS, "Netral")
    data["emosi"] = _coerce(data.get("emosi"), EMOSI_VALS, "Netral/Datar")
    data["resolusi"] = _coerce(data.get("resolusi"), RESOLUSI_VALS, "Belum Terselesaikan")
    data["frustrasi"] = bool(data.get("frustrasi"))
    out["analysis"] = data
    out["ok"] = True
    return out
