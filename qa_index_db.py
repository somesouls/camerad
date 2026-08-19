# -*- coding: utf-8 -*-
"""qa_index_db.py — Indeks Q&A historis (Sosmed FAQ + Livechat AWE) — Fase 5/6.

Gagasan: pertanyaan user baru bahasanya MIRIP pertanyaan historis. Jadi yang
di-embed adalah PERTANYAAN (bukan jawaban) — question-to-question (Q2Q).
Jawaban historis hanya jadi jembatan: rujukan peraturan yang terdeteksi di
dalamnya (regref) diresolusi ke basis peraturan yang rapi.

Fase 6: tiap pasangan kini menyimpan conv_id (id percakapan/utas asal), sehingga
hasil Q2Q bisa DIEKSPANSI dengan tanya-jawab lanjutan dalam utas yang sama
("penggalian sampai bawah") via siblings(). Pasangan nyaris-tanpa-isi
(sapaan saja, < 3 token bermakna) dibuang saat build.

Penyimpanan (pola peraturan_db / sop_db):
  * qa_unit : id, sumber ('sosmed'|'awe'), ref_id, conv_id, question, answer,
              topik, url, reg_json (rujukan terdeteksi+teresolusi saat build),
              created_at
  * qa_vec  : id, dim, emb (BLOB float32; cosine numpy, tanpa sqlite-vec)
  * qa_meta : key/value (penanda build)

Keamanan & kualitas:
  * AWE: bot-filter dari awe_botfilter_patch dipakai ulang (giliran Bot/CCAI &
    percakapan full-bot dibuang); hanya giliran pelanggan + petugas manusia.
  * PII: question & answer di-mask pii_mask.mask_text SEBELUM disimpan.
  * Dedup per (sumber, pertanyaan ternormalisasi).
  * Resume: unit yang sudah punya vektor berdimensi model aktif dilewati.

DB file: env PIPELINE_QA_DB_FILE (default qa.db). Stdlib + modul repo.
"""
import os
import json
import sqlite3

import peraturan_semantic as psem
import regref

try:
    import numpy as np
except Exception:            # pragma: no cover
    np = None
try:
    import pii_mask
except Exception:            # pragma: no cover
    pii_mask = None
try:
    import text_norm as tnorm
except Exception:            # pragma: no cover
    tnorm = None

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_BUSY_TIMEOUT_MS = 30000


def default_db_path():
    return os.environ.get("PIPELINE_QA_DB_FILE") or os.path.join(_BASE_DIR, "qa.db")


def connect(db_path=None):
    conn = sqlite3.connect(db_path or default_db_path(), timeout=_BUSY_TIMEOUT_MS / 1000.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=%d;" % _BUSY_TIMEOUT_MS)
    return conn


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS qa_unit (
            id          TEXT PRIMARY KEY,
            sumber      TEXT,
            ref_id      TEXT,
            conv_id     TEXT DEFAULT '',
            question    TEXT,
            answer      TEXT,
            topik       TEXT,
            url         TEXT,
            reg_json    TEXT,
            created_at  TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_qa_sumber ON qa_unit(sumber);
        CREATE INDEX IF NOT EXISTS idx_qa_conv ON qa_unit(conv_id);

        CREATE TABLE IF NOT EXISTS qa_vec (
            id  TEXT PRIMARY KEY,
            dim INTEGER,
            emb BLOB
        );

        CREATE TABLE IF NOT EXISTS qa_meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )
    # Migrasi ringan (Fase 6): DB lama belum punya kolom conv_id.
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(qa_unit)").fetchall()]
        if "conv_id" not in cols:
            conn.execute("ALTER TABLE qa_unit ADD COLUMN conv_id TEXT DEFAULT ''")
    except Exception:
        pass
    conn.commit()
    return conn


# ------------------------------------------------------------------ utilitas
def _mask(t):
    if pii_mask is None:
        return t
    try:
        return pii_mask.mask_text(t)
    except Exception:
        return t


def _norm_q(t):
    if tnorm is not None:
        try:
            return tnorm.normalize(t)
        except Exception:
            pass
    return (t or "").lower()


def _cukup_informatif(q):
    """Buang pasangan nyaris-tanpa-isi (sapaan/ucapan saja) agar tidak mencemari
    hasil Q2Q: wajib >= 3 token bermakna pasca-normalisasi."""
    try:
        return len([t for t in _norm_q(q).split() if t]) >= 3
    except Exception:
        return True


def _regs(conn_per, text):
    """Deteksi + resolusi rujukan di teks; simpan ringkas (label + identitas
    dokumen hasil resolve)."""
    out = []
    if conn_per is None:
        return out
    try:
        for d in regref.detect_resolve(text, conn_per):
            m = d.get("match")
            out.append({
                "label": d.get("label") or d.get("raw") or "",
                "matched": bool(m),
                "jenis": (m or {}).get("jenis") or d.get("jenis"),
                "nomor": (m or {}).get("nomor") or "",
                "tahun": (m or {}).get("tahun") or d.get("tahun"),
                "source_id": (m or {}).get("source_id") or "",
                "status": (m or {}).get("status") or "",
            })
    except Exception:
        pass
    return out


# ------------------------------------------------------------------ kolektor
def collect_sosmed(limit=2000):
    """Pasangan Q&A dari FAQ Sosmed terjawab (balasan akun resmi).
    Fase 6: conv_id = conversation_id utas X (untuk ekspansi utas)."""
    out = []
    try:
        import sosmed_db as sdb
    except Exception:
        return out
    c = None
    try:
        c = sdb.init_db(sdb.connect())
        fp = sdb.faq_pairs(c, only_answered=True, limit=int(limit))
        for p in (fp.get("pairs") or []):
            q = (p.get("pertanyaan") or "").strip()
            a = (p.get("jawaban_draf") or "").strip()
            if not q or not a:
                continue
            rid = "%s:%s" % (p.get("platform") or "x", p.get("external_id") or p.get("id") or "")
            out.append({"sumber": "sosmed", "ref_id": rid,
                        "conv_id": str(p.get("conversation_id") or ""),
                        "question": q,
                        "answer": a, "topik": str(p.get("topik") or ""),
                        "url": str(p.get("permalink") or "")})
    except Exception:
        pass
    finally:
        try:
            if c is not None:
                c.close()
        except Exception:
            pass
    return out


def collect_awe(limit=1500):
    """Pasangan Q&A dari percakapan AWE: giliran pelanggan -> pertanyaan,
    giliran petugas manusia -> jawaban. Bot/CCAI & full-bot dibuang (helper
    awe_botfilter_patch dipakai ulang). conv_id = sid percakapan."""
    out = []
    try:
        import avaya_db as avdb
    except Exception:
        return out
    try:
        import awe_botfilter_patch as bf
        _is_bot_agent, _is_bot_turn = bf._is_bot_agent, bf._is_bot_turn
    except Exception:
        def _is_bot_agent(name):
            n = (name or "").lower()
            return any(k in n for k in ("chatbot", "ccai", "virtual assistant", "google"))

        def _is_bot_turn(role, text):
            return (role or "").strip().lower() in ("bot", "ccai")
    c = None
    try:
        c = avdb.init_db(avdb.connect())
        rows = c.execute(
            "SELECT sid, jenis_layanan, topik, agent_name, transkrip_json "
            "FROM awe_conversations WHERE transkrip_json IS NOT NULL "
            "ORDER BY tanggal DESC LIMIT ?", (int(limit),)).fetchall()
    except Exception:
        rows = []
    finally:
        try:
            if c is not None:
                c.close()
        except Exception:
            pass
    for r in rows:
        try:
            d = dict(r)
            if _is_bot_agent(d.get("agent_name")):
                continue
            try:
                tx = json.loads(d.get("transkrip_json") or "[]")
            except Exception:
                continue
            cust, agent = [], []
            for seg in tx:
                if not isinstance(seg, dict):
                    continue
                role, text = seg.get("role", ""), seg.get("text", "")
                if not text:
                    continue
                if _is_bot_turn(role, text):
                    continue
                try:
                    is_agent = avdb._is_agent(role, text)
                except Exception:
                    is_agent = False
                (agent if is_agent else cust).append(str(text))
            q = " ".join(cust).strip()
            a = " ".join(agent).strip()
            if not q or not a:
                continue
            sid = str(d.get("sid") or "")
            out.append({"sumber": "awe", "ref_id": "awe:%s" % sid, "conv_id": sid,
                        "question": q, "answer": a,
                        "topik": str(d.get("jenis_layanan") or d.get("topik") or ""),
                        "url": ""})
        except Exception:
            continue
    return out


# --------------------------------------------------------------------- build
def build_index(batch=64, limit_sosmed=2000, limit_awe=1500, progress=True):
    """Bangun/isi indeks Q&A (idempoten + resume per dimensi model)."""
    if np is None:
        return {"ok": False, "error": "numpy tidak tersedia."}
    if not psem.is_available():
        return {"ok": False, "error": "Model embedding tidak tersedia."}
    conn = init_db(connect())
    conn_per = None
    try:
        import peraturan_db as pdb
        conn_per = pdb.init_db(pdb.connect())
    except Exception:
        conn_per = None
    try:
        try:
            dim_model = int(psem.embed_dim() or 0)
        except Exception:
            dim_model = 0
        have = set()
        if dim_model:
            for r in conn.execute("SELECT id FROM qa_vec WHERE dim=?",
                                  (dim_model,)).fetchall():
                have.add(r["id"])

        mentah = collect_sosmed(limit_sosmed) + collect_awe(limit_awe)
        n_awal = len(mentah)
        mentah = [it for it in mentah if _cukup_informatif(it.get("question") or "")]
        n_buang = n_awal - len(mentah)
        # dedup per (sumber, pertanyaan ternormalisasi); simpan jawaban terpanjang
        per_key = {}
        for it in mentah:
            key = (it["sumber"], _norm_q(it["question"]))
            if key not in per_key or len(it["answer"]) > len(per_key[key]["answer"]):
                per_key[key] = it
        items = list(per_key.values())
        if progress:
            print("[qa_index_db] koleksi: %d mentah -> %d unik (sosmed+awe); "
                  "nyaris-tanpa-isi dibuang: %d" % (n_awal, len(items), n_buang),
                  flush=True)

        n_new = n_vec = n_skip = 0
        todo = []
        for it in items:
            qid = "%s:%s" % (it["sumber"], it["ref_id"])
            q_m = _mask(it["question"])
            a_m = _mask(it["answer"])
            regs = _regs(conn_per, q_m + " " + a_m)
            conn.execute(
                "INSERT INTO qa_unit(id, sumber, ref_id, conv_id, question, answer, topik, url, reg_json) "
                "VALUES(?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET question=excluded.question, "
                "answer=excluded.answer, topik=excluded.topik, url=excluded.url, "
                "conv_id=excluded.conv_id, reg_json=excluded.reg_json",
                (qid, it["sumber"], it["ref_id"], it.get("conv_id") or "",
                 q_m, a_m, it.get("topik") or "",
                 it.get("url") or "", json.dumps(regs, ensure_ascii=False)),
            )
            if qid in have:
                n_skip += 1
            else:
                todo.append((qid, q_m))
            n_new += 1
            if n_new % 500 == 0:
                conn.commit()
        conn.commit()
        _vec_cache_clear()

        for i in range(0, len(todo), batch):
            chunk = todo[i:i + batch]
            arr = psem.embed_passages([t for _, t in chunk])
            if arr is None:
                continue
            for (qid, _), v in zip(chunk, arr):
                blob = psem.to_blob(v)
                if blob is None:
                    continue
                conn.execute("DELETE FROM qa_vec WHERE id=?", (qid,))
                conn.execute("INSERT INTO qa_vec(id, dim, emb) VALUES (?,?,?)",
                             (qid, int(len(v)), blob))
                n_vec += 1
            conn.commit()
            if progress:
                print("[qa_index_db] embed: %d/%d" % (min(i + batch, len(todo)), len(todo)),
                      flush=True)
        conn.execute(
            "INSERT INTO qa_meta(key, value) VALUES('last_build', datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value")
        conn.commit()
        _vec_cache_clear()
        return {"ok": True, "unit": n_new, "vec_baru": n_vec, "vec_skip": n_skip,
                "unik": len(items), "buang_pendek": n_buang}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    finally:
        try:
            conn.close()
        except Exception:
            pass
        try:
            if conn_per is not None:
                conn_per.close()
        except Exception:
            pass


# --------------------------------------------------------------- pencarian
_VEC_CACHE = {"sig": None, "ids": None, "mat": None}


def _vec_cache_clear():
    _VEC_CACHE["sig"] = None
    _VEC_CACHE["ids"] = None
    _VEC_CACHE["mat"] = None


def _load_vectors(conn):
    if np is None:
        return [], None
    try:
        sig = conn.execute("SELECT COUNT(*), COALESCE(MAX(rowid),0) FROM qa_vec").fetchone()
        sig = (int(sig[0]), int(sig[1]))
    except Exception:
        sig = (0, 0)
    if _VEC_CACHE["sig"] == sig and _VEC_CACHE["mat"] is not None:
        return _VEC_CACHE["ids"], _VEC_CACHE["mat"]
    try:
        rows = conn.execute("SELECT id, emb FROM qa_vec").fetchall()
    except Exception:
        return [], None
    ids, vecs = [], []
    for r in rows:
        v = psem.from_blob(r["emb"])
        if v is None:
            continue
        ids.append(r["id"])
        vecs.append(v)
    mat = np.vstack(vecs) if vecs else None
    _VEC_CACHE["sig"] = sig
    _VEC_CACHE["ids"] = ids
    _VEC_CACHE["mat"] = mat
    return ids, mat


def min_cos():
    try:
        return float(os.environ.get("RAG_QA_MIN_COS", "0.50"))
    except Exception:
        return 0.50


def search(query, k=3, conn=None):
    """Q2Q: kembalikan pasangan paling mirip PERTANYAANNYA (cos >= RAG_QA_MIN_COS)."""
    own = conn is None
    conn = conn or init_db(connect())
    try:
        if np is None:
            return []
        q = (query or "").strip()
        if not q:
            return []
        qv = psem.embed_query(q)
        if qv is None:
            return []
        ids, mat = _load_vectors(conn)
        if mat is None or not ids:
            return []
        sims = mat @ np.asarray(qv, dtype="float32")
        order = np.argsort(-sims)[: max(int(k or 3) * 3, 10)]
        mc = min_cos()
        out = []
        for i in order:
            c = float(sims[int(i)])
            if c < mc:
                continue
            r = conn.execute("SELECT * FROM qa_unit WHERE id=?",
                             (ids[int(i)],)).fetchone()
            if not r:
                continue
            d = dict(r)
            d["cos"] = round(c, 4)
            try:
                d["regs"] = json.loads(d.pop("reg_json") or "[]")
            except Exception:
                d.pop("reg_json", None)
                d["regs"] = []
            out.append(d)
            if len(out) >= int(k or 3):
                break
        return out
    finally:
        if own:
            conn.close()


def siblings(conv_id, exclude_id, sumber="sosmed", limit=4, conn=None):
    """Fase 6: pasangan lain dalam percakapan/utas yang sama (ekspansi
    'penggalian sampai bawah'). Diurutkan kronologis mendekati: ref_id sosmed
    memuat external_id (snowflake X) dengan panjang digit seragam sehingga
    urutan leksikal ~ urutan waktu."""
    if not conv_id:
        return []
    own = conn is None
    conn = conn or init_db(connect())
    try:
        rows = conn.execute(
            "SELECT id, question, answer, url FROM qa_unit "
            "WHERE conv_id=? AND sumber=? AND id != ? ORDER BY ref_id ASC LIMIT ?",
            (str(conv_id), sumber, str(exclude_id or ""), int(limit or 4))).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        if own:
            conn.close()


def stats(conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        def _c(sql):
            return conn.execute(sql).fetchone()[0] or 0
        return {"total_unit": _c("SELECT COUNT(*) FROM qa_unit"),
                "total_vec": _c("SELECT COUNT(*) FROM qa_vec"),
                "sosmed": _c("SELECT COUNT(*) FROM qa_unit WHERE sumber='sosmed'"),
                "awe": _c("SELECT COUNT(*) FROM qa_unit WHERE sumber='awe'"),
                "dengan_utas": _c("SELECT COUNT(DISTINCT conv_id) FROM qa_unit WHERE conv_id != ''"),
                "dengan_rujukan": _c("SELECT COUNT(*) FROM qa_unit WHERE reg_json NOT IN ('','[]') AND reg_json IS NOT NULL")}
    finally:
        if own:
            conn.close()
