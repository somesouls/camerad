# -*- coding: utf-8 -*-
"""avaya/phone_glossary.py - Ambil istilah dari Glosarium Pajak internal
(knowledge.glossary_db) dan bentuk blok koreksi STT untuk prompt LLM telepon.

Dipisah dari phone_llm.py agar berkas tetap kecil dan aman di-push. Semua
kegagalan (modul/DB tak tersedia) dikembalikan sebagai string kosong supaya
analisis LLM tetap jalan memakai fallback statis di phone_llm.py.
"""
_NL = chr(10)
_CACHE = {}

# Istilah domain inti yang WAJIB dikenali STT walau belum tentu ada di
# Glosarium Pajak (mis. MELATI/Kring Pajak tidak termasuk seed DB). Dipakai
# sebagai konteks (hotwords) untuk membiaskan Qwen3-ASR agar tak salah dengar.
_ASR_INTI = [
    "Kring Pajak", "MELATI", "NPWP", "NIK", "EFIN", "SPT", "PPh", "PPN",
    "PKP", "e-Faktur", "e-Bupot", "DJP Online", "Coretax", "Korpex",
    "kode billing", "NTPN", "dukcapil", "faktur pajak", "wajib pajak",
    "aktivasi akun", "bukti potong", "SP2DK",
]


def _load(max_terms=90, max_alias=6):
    """Baca istilah 'aktif' dari Glosarium Pajak, urut prioritas, jadi teks."""
    try:
        from knowledge import glossary_db as gdb
    except Exception:
        return ""
    c = None
    terms = []
    try:
        c = gdb.init_db(gdb.connect())
        terms = gdb.list_terms(c, status="aktif", limit=max_terms)
    except Exception:
        terms = []
    finally:
        try:
            if c is not None:
                c.close()
        except Exception:
            pass
    lines = []
    for t in terms or []:
        term = str((t or {}).get("term") or "").strip()
        if not term:
            continue
        nama = str(t.get("nama_panjang") or "").strip()
        head = term + ((" (" + nama + ")") if nama else "")
        al = [str(x).strip() for x in (t.get("aliases") or []) if str(x).strip()]
        al = [x for x in al if x.lower() != term.lower()][:max_alias]
        if al:
            lines.append("- %s; sering ter-STT sebagai: %s" % (head, ", ".join(al)))
        else:
            lines.append("- %s" % head)
    if not lines:
        return ""
    header = (
        "Glosarium istilah pajak (sumber: Glosarium Pajak internal) untuk "
        "mengoreksi salah dengar STT. Bila sebuah kata mirip bunyi dengan salah "
        "satu istilah baku di bawah, tafsirkan sebagai istilah itu; jangan "
        "paksakan bila konteks jelas menunjukkan makna lain:")
    return _NL.join([header] + lines)


def glossary_block():
    """Blok glosarium dinamis (di-cache di memori proses). '' bila tak ada."""
    txt = _CACHE.get("txt")
    if txt:
        return txt
    txt = _load()
    if txt:
        _CACHE["txt"] = txt
    return txt


def _terms(max_terms=150, status="aktif"):
    """Daftar dict istilah dari Glosarium Pajak; [] bila modul/DB tak ada."""
    try:
        from knowledge import glossary_db as gdb
    except Exception:
        return []
    c = None
    try:
        c = gdb.init_db(gdb.connect())
        return list(gdb.list_terms(c, status=status, limit=max_terms) or [])
    except Exception:
        return []
    finally:
        try:
            if c is not None:
                c.close()
        except Exception:
            pass


def asr_context(max_terms=150):
    """Daftar kosakata ringkas (hotwords) untuk membiaskan STT Qwen3-ASR.

    Sengaja berupa daftar kata kunci baku (bukan kalimat) sesuai anjuran
    Qwen3-ASR: gabungan istilah inti statis + istilah 'aktif' Glosarium Pajak
    (term + nama panjang). Alias 'salah dengar' TIDAK disertakan agar model
    tidak terbias ke ejaan yang keliru. Di-cache; '' bila benar-benar kosong.
    """
    cached = _CACHE.get("asr")
    if cached is not None:
        return cached
    vocab = []
    seen = set()

    def _add(w):
        w = str(w or "").strip()
        if not w:
            return
        k = w.lower()
        if k in seen:
            return
        seen.add(k)
        vocab.append(w)

    for w in _ASR_INTI:
        _add(w)
    for t in _terms(max_terms):
        _add(str((t or {}).get("term") or "").strip())
        _add(str((t or {}).get("nama_panjang") or "").strip())
    txt = ""
    if vocab:
        txt = "Kosakata domain pajak (layanan Kring Pajak): " + ", ".join(vocab) + "."
    _CACHE["asr"] = txt
    return txt
