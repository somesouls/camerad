# -*- coding: utf-8 -*-
"""avaya/phone_llm.py - Lapisan LLM: rapikan + ringkas + analisis transkrip Telepon.

Mengubah transkrip STT yang masih kasar (audio telepon 8 kHz) menjadi keluaran
terstruktur: dialog 2 penutur yang rapi, ringkasan, topik, sentimen, emosi,
resolusi, dan entitas. Dipakai probe_llm.py (uji terminal) dan nanti jalur web.

Backend LLM = common.llm_client (provider via .env: openai/azure/gemini/local).
Untuk data pajak yang sensitif, set LLM_PROVIDER=local (vLLM Qwen) agar
transkrip TIDAK keluar dari mesin.

Taksonomi diselaraskan dengan pipeline Chat (avaya/pipeline.py):
  sentimen: Positif | Negatif | Netral
  emosi   : Senang/Bahagia | Marah/Frustrasi | Sedih/Kecewa | Takut/Cemas |
            Terkejut | Jijik/Bosan | Netral/Datar
  resolusi: Terselesaikan | Belum Terselesaikan
"""
import json
import os

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
    '- entitas: objek {"nama":[...],"nomor":[...],"lainnya":[...]}.',
    "- poin_penting: array kalimat pendek.",
    "- catatan_kualitas: catatan singkat soal bagian transkrip yang diragukan.",
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


def _coerce(val, allowed, default):
    v = str(val or "").strip().lower()
    for a in allowed:
        if v == a.lower():
            return a
    return default


def _build_user(text, segments):
    parts = ["Transkrip mentah STT:", (text or "(kosong)").strip(), ""]
    if segments:
        parts.append("Segmen berwaktu (detik):")
        for s in segments[:80]:
            try:
                parts.append("[%.1f-%.1f] %s" % (
                    float(s.get("start") or 0.0),
                    float(s.get("end") or 0.0),
                    (s.get("text") or "").strip()))
            except Exception:
                continue
        parts.append("")
    parts.append(_INSTRUKSI)
    return _NL.join(parts)


def analyze_transcript(text, segments=None, max_new_tokens=1500, temperature=0.2):
    """Rapikan + analisis transkrip via LLM. Kembalikan dict aman-JSON.

    Bentuk: {ok, provider, model, analysis{...}, raw, error?}
    """
    out = {"ok": False, "provider": (os.environ.get("LLM_PROVIDER") or "openai"),
           "model": "", "analysis": None, "raw": ""}
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
    try:
        data = json.loads(_strip_json(reply))
    except Exception as e:
        out["error"] = "Keluaran LLM bukan JSON valid: %r" % e
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
