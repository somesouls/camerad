# -*- coding: utf-8 -*-
"""eval_holdout.py — Hold-out PER-PERCAKAPAN untuk evaluasi RAG.

Menggantikan hold-out lama yang membuang SELURUH sumber AWE saat menilai sampel
livechat. Kelemahan cara lama: pengetahuan prosedural (aplikasi) sebagian besar
hidup di AWE, sehingga membuangnya membuat mesin kehilangan seluruh sumber dan
abstain berlebihan -> keandalan livechat/aplikasi tertekan secara artifisial.

Cara baru: sumber AWE tetap dipakai, tetapi HANYA satu percakapan (SID) yang
sedang diuji yang dikecualikan dari retrieval, agar mesin tidak "mencontek"
balasan agennya sendiri (anti-kebocoran) sambil tetap boleh belajar dari
percakapan AWE lain (uji generalisasi, bukan hafalan).

Mekanisme: monkeypatch rag_engine._DISPATCH["awe"] (pola sama seperti
rag_successor_patch / rag_calibration_patch). SID aktif disimpan THREAD-LOCAL
sehingga run paralel tak saling menimpa. Bila SID tidak diset (mis. produksi),
delegasikan ke fungsi asli -> perilaku produksi TIDAK berubah.

Pakai:
  import evaluation.holdout as eval_holdout
  eval_holdout.set_holdout_sid(sid)   # sebelum memanggil rag_engine.answer
  ...
  eval_holdout.reset_holdout_sid()    # sesudahnya (harness memakai finally)
"""
import json
import threading

import rag.engine as _re

try:
    import avaya.db as avdb
except Exception:            # pragma: no cover
    avdb = None

_tl = threading.local()


def set_holdout_sid(sid):
    """Set SID percakapan AWE yang harus dikecualikan pada thread ini."""
    _tl.sid = (str(sid).strip() if sid else "")


def reset_holdout_sid():
    """Kosongkan hold-out pada thread ini (kembali ke perilaku normal)."""
    _tl.sid = ""


def current_holdout_sid():
    return getattr(_tl, "sid", "") or ""


# Simpan referensi fungsi AWE asli SEKALI (agar delegasi produksi byte-identik).
_orig_awe = _re._DISPATCH.get("awe")


def _ctx_awe_holdout(q, limit=3):
    """Varian _ctx_awe yang mengecualikan SID hold-out SEBELUM pemotongan
    top-N, sehingga percakapan AWE lain otomatis mengisi slot (backfill).

    Tanpa SID aktif -> pakai fungsi asli apa adanya (produksi tak berubah).
    Logika retrieval menyalin rag_engine._ctx_awe dan hanya menambahkan satu
    baris penyaringan SID; helper (_tokens/_clip) dan _is_agent dipakai ulang.
    """
    excl = current_holdout_sid()
    if not excl or avdb is None or _orig_awe is None:
        return _orig_awe(q, limit=limit) if _orig_awe else ("", [])
    toks = _re._tokens(q, k=10)
    if not toks:
        return "", []
    try:
        c = avdb.init_db(avdb.connect())
    except Exception:
        return "", []
    try:
        cond = ("(COALESCE(jenis_layanan,'') LIKE ? OR COALESCE(mapped_intent,'') "
                "LIKE ? OR COALESCE(topik,'') LIKE ? OR COALESCE(transkrip_json,'') LIKE ?)")
        where = " OR ".join([cond] * len(toks))
        params = []
        for t in toks:
            params += ["%" + t + "%"] * 4
        sql = ("SELECT sid,tanggal,customer,agent_name,mapped_intent,jenis_layanan,"
               "topik,transkrip_json FROM awe_conversations "
               "WHERE transkrip_json IS NOT NULL AND (" + where + ") LIMIT 400")
        rows = c.execute(sql, params).fetchall()
    except Exception:
        rows = []
    finally:
        try:
            c.close()
        except Exception:
            pass
    scored = []
    for r in rows:
        d = dict(r)
        if str(d.get("sid") or "").strip() == excl:
            continue    # HOLD-OUT: percakapan yang sedang diuji, jangan dipakai
        hay = " ".join([str(d.get("jenis_layanan") or ""), str(d.get("mapped_intent") or ""),
                        str(d.get("topik") or ""), str(d.get("transkrip_json") or "")]).lower()
        score = sum(hay.count(t) for t in toks)
        if score > 0:
            scored.append((score, d))
    scored.sort(key=lambda x: -x[0])
    blocks, sources = [], []
    for score, d in scored[:limit]:
        try:
            tx = json.loads(d.get("transkrip_json") or "[]")
        except Exception:
            tx = []
        cust, agent = [], []
        for seg in tx:
            if not isinstance(seg, dict):
                continue
            role = seg.get("role", "")
            text = seg.get("text", "")
            if not text:
                continue
            try:
                is_agent = avdb._is_agent(role, text)
            except Exception:
                is_agent = False
            (agent if is_agent else cust).append(str(text))
        if not agent:
            continue
        label = d.get("jenis_layanan") or d.get("mapped_intent") or d.get("topik") or "Percakapan AWE"
        blocks.append("Topik: %s\nPertanyaan pelanggan: %s\nJawaban petugas: %s"
                      % (label, _re._clip(" ".join(cust), 300) or "-", _re._clip(" ".join(agent), 500)))
        sources.append({"sumber": "Percakapan AWE", "judul": str(label),
                        "ref": ("SID " + str(d.get("sid") or "")).strip()})
    return "\n\n".join(blocks), sources


def apply():
    """Pasang patch ke dispatch AWE. Idempoten."""
    if _re._DISPATCH.get("awe") is not _ctx_awe_holdout:
        _re._DISPATCH["awe"] = _ctx_awe_holdout
    return True


apply()
