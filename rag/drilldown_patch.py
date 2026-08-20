# -*- coding: utf-8 -*-
"""rag_drilldown_patch.py — Fase 6: penelusuran ketentuan PELAKSANA (drill-down).

Usulan pengguna (19 Agu 2026): aturan tingkat atas (UU/PP) sifatnya umum —
detail teknisnya biasanya ada di PMK/PER/SE di bawahnya. Maka bila kandidat
teratas retrieval peraturan berlevel UU/PERPU/PERPRES/PP, patch ini mencari
dokumen BERLEVEL LEBIH RENDAH yang TERVERIFIKASI merujuk nomor induknya, dan
menyertakannya sebagai blok "Ketentuan pelaksana" — jembatan dari aturan umum
ke aturan teknisnya, otomatis.

Cara verifikasi (presisi dulu, baru recall):
  1. Prefilter SQL ringan: unit yang isi-nya memuat digit nomor + tahun induk.
  2. Verifikasi ketat: regref.detect() (regex multi-format: "PP 111 Tahun
     2000", "PP Nomor 111/2000", dst.) pada isi kandidat — harus cocok persis
     (jenis + nomor utama + tahun) dengan induk.
  3. Syarat level: kekuatan_hukum kandidat < induk (peta level internal).
Catatan: pencarian kandidat sengaja langsung via SQL (bukan pdb.search) agar
kebal terhadap gerbang cosine RAG_MIN_COS — dokumen pelaksana memang tidak
selalu mirip secara semantik dengan query, tetapi MERUJUK induknya.

Membungkus _ctx_peraturan versi terakhir (successor v2). Fail-soft penuh.
Env: RAG_DRILLDOWN=0 matikan; RAG_DRILLDOWN_MAX=2 jumlah maks disertakan.
Diimpor di web_app.py SETELAH rag_domain_patch.
"""
import os
import re

import rag.engine as _re

try:
    import peraturan.db as _pdb
except Exception:            # pragma: no cover
    _pdb = None
try:
    import common.regref as _regref
except Exception:            # pragma: no cover
    _regref = None

_orig = getattr(_re, "_ctx_peraturan", None)

_LEVEL = {"UU": 100, "PERPU": 95, "PERPRES": 90, "PP": 80,
          "PMK": 60, "KMK": 55, "PER": 50, "KEP": 50, "SE": 40}
_MIN_PARENT = 80  # hanya UU/PERPU/PERPRES/PP yang memicu drill-down
_RE_FIRST_NUM = re.compile(r"(\d{1,3})")


def _on():
    return str(os.environ.get("RAG_DRILLDOWN", "1")).strip().lower() not in (
        "0", "false", "no", "off")


def _max_n():
    try:
        return max(1, int(os.environ.get("RAG_DRILLDOWN_MAX", "2")))
    except Exception:
        return 2


def _head(d):
    jenis = str(d.get("jenis_peraturan") or "").strip()
    nomor = str(d.get("nomor") or "").strip()
    tahun = str(d.get("tahun") or "").strip()
    head = " ".join(x for x in [jenis, nomor,
                                ("Tahun " + tahun) if tahun else ""] if x).strip()
    return head or "Peraturan"


def _find_pelaksana(p_jenis, p_num, p_tahun, p_level, p_sid, maks):
    """Daftar dokumen berlevel lebih rendah yang terverifikasi merujuk induk.
    Tiap entri: dict identitas dokumen + cuplikan unit yang memuat rujukan."""
    if _pdb is None or _regref is None or not p_num or not p_tahun:
        return []
    conn = None
    try:
        conn = _pdb.init_db(_pdb.connect())
        rows = conn.execute(
            "SELECT source_id, jenis_peraturan, nomor, tahun, judul, isi "
            "FROM peraturan_unit WHERE status='berlaku' AND isi LIKE ? "
            "LIMIT 500",
            ("%" + p_num + "%" + p_tahun + "%",)).fetchall()
    except Exception:
        rows = []
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass
    per_doc = {}
    for r in rows:
        try:
            d = r if isinstance(r, dict) else dict(r)
        except Exception:
            continue
        sid = str(d.get("source_id") or "")
        if not sid or sid == p_sid or sid in per_doc:
            continue
        jl = _LEVEL.get(str(d.get("jenis_peraturan") or "").strip().upper(), 0)
        if not jl or jl >= p_level:
            continue
        isi = str(d.get("isi") or "")
        ok = False
        try:
            for ref in _regref.detect(isi):
                if (ref.get("jenis") == p_jenis
                        and str(ref.get("num") or "").lstrip("0") == str(p_num).lstrip("0")
                        and str(ref.get("tahun") or "") == str(p_tahun)):
                    ok = True
                    break
        except Exception:
            ok = False
        if not ok:
            continue
        per_doc[sid] = d
        if len(per_doc) >= int(maks or 2):
            break
    return list(per_doc.values())


def _ctx_peraturan_dd(q, limit=4):
    text, sources = ("", [])
    if _orig is not None:
        try:
            text, sources = _orig(q, limit)
        except Exception:
            text, sources = "", []
    if not _on() or _pdb is None:
        return text, sources
    try:
        tops = _pdb.search(q, 1, ("berlaku",)) or []
    except Exception:
        tops = []
    if not tops:
        return text, sources
    try:
        top = tops[0] if isinstance(tops[0], dict) else dict(tops[0])
    except Exception:
        return text, sources
    p_jenis = str(top.get("jenis_peraturan") or "").strip().upper()
    p_level = _LEVEL.get(p_jenis, 0)
    p_nomor = str(top.get("nomor") or "").strip()
    if p_level < _MIN_PARENT or not p_nomor:
        return text, sources
    mn = _RE_FIRST_NUM.search(p_nomor)
    p_num = mn.group(1) if mn else ""
    p_tahun = str(top.get("tahun") or "").strip()
    mt = re.search(r"((?:19|20)\d{2})", p_nomor)
    if not p_tahun and mt:
        p_tahun = mt.group(1)
    picked = _find_pelaksana(p_jenis, p_num, p_tahun, p_level,
                             str(top.get("source_id") or ""), _max_n())
    if not picked:
        return text, sources
    blocks = [text] if text else []
    for d in picked:
        head = _head(d)
        judul = _re._clip(str(d.get("judul") or "").strip(), 220)
        isi = _re._clip(str(d.get("isi") or "").strip(), 500)
        blok = ("Ketentuan pelaksana yang merujuk %s %s (aturan teknis di bawahnya):\n%s"
                % (p_jenis, p_nomor, head))
        if judul:
            blok += "\nTentang: " + judul
        if isi:
            blok += "\nCuplikan rujukan: " + isi
        blocks.append(blok)
        sources.append({"sumber": "Peraturan",
                        "judul": head + " (ketentuan pelaksana)",
                        "ref": str(d.get("reference") or d.get("hierarchy") or ""),
                        "url": str(d.get("source_url") or "")})
    return "\n\n".join(blocks), sources


def _install():
    if not _on():
        print("[rag_drilldown_patch] dimatikan (RAG_DRILLDOWN=0).", flush=True)
        return
    if getattr(_re, "_drilldown_patched", False):
        return
    if _orig is None:
        return
    try:
        _re._ctx_peraturan = _ctx_peraturan_dd
        if isinstance(getattr(_re, "_DISPATCH", None), dict):
            _re._DISPATCH["peraturan"] = _ctx_peraturan_dd
    except Exception as e:
        print("[rag_drilldown_patch] gagal memasang: %s" % e, flush=True)
        return
    _re._drilldown_patched = True
    print("[rag_drilldown_patch] drill-down ketentuan pelaksana aktif "
          "(UU/PP -> PMK/PER di bawahnya; maks=%d)." % _max_n(), flush=True)


_install()
