# -*- coding: utf-8 -*-
"""
intentmap_db.py
---------------
Lapisan penyimpanan (SQLite) untuk **Peta Intent & Maksud Analis**.

Ini pengetahuan jenis KETIGA (setelah glosarium & disambiguasi). Isinya adalah
KEPUTUSAN SENGAJA analis dalam memetakan utterance ke intent Dialogflow, yang
sering MELAWAN logika semantik biasa sehingga LLM mustahil menebaknya. Contoh:
  - "lupa email dan no hp" -> intent "Perubahan Data" (bukan sekadar keluhan login)
  - Semua pertanyaan UMKM -> satu megaintent "UMKM" + bridging intent anakan,
    karena memecahnya berisiko menaikkan fallback/MKTA (keterbatasan Dialogflow).

Berfungsi sebagai "kunci jawaban / pedoman anotasi" bagi LLM saat menilai
fallback & MKTA, agar vonisnya selaras dengan maksud analis, bukan semantik naif.

Disimpan di database yang sama (PIPELINE_DB_FILE) pada tabel `intentmap`.
Hanya memakai stdlib.
"""
import json
import re
import datetime as _dt

import db.analytics_db as adb  # pakai ulang connect() / default_db_path()

KATEGORI = [
    "Identitas", "Autentikasi", "Perubahan Data", "Pelaporan",
    "Pembayaran", "Faktur", "UMKM", "Layanan/Kanal", "Umum",
]
# Struktur intent secara praktik Dialogflow.
STRUKTUR = ["mandiri", "megaintent", "bridging_child"]
STATUS = ["aktif", "usang"]

# Prioritas: 5 tingkat ramah non-teknis, dipetakan ke angka 0..100 di belakang layar.
# Angka dipakai untuk urutan tampil & "siapa menang" saat beberapa aturan cocok.
PRIORITAS = [
    ("Sangat Tinggi", 90),
    ("Tinggi", 70),
    ("Sedang", 50),
    ("Rendah", 30),
    ("Sangat Rendah", 10),
]
PRIORITAS_LABELS = [p[0] for p in PRIORITAS]
_PRIORITAS_MAP = {k.lower(): v for k, v in PRIORITAS}


def num_to_prioritas(n):
    """Angka -> label tingkat (berbasis rentang, toleran nilai lama)."""
    try:
        n = int(n or 0)
    except Exception:
        n = 0
    if n >= 80:
        return "Sangat Tinggi"
    if n >= 60:
        return "Tinggi"
    if n >= 40:
        return "Sedang"
    if n >= 20:
        return "Rendah"
    return "Sangat Rendah"


def prioritas_to_num(v, default=50):
    """Terima label ('Tinggi') ATAU angka -> angka 0..100. Default 'Sedang' (50)."""
    if v is None or v == "":
        return default
    if isinstance(v, bool):
        return default
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip()
    if s.lower() in _PRIORITAS_MAP:
        return _PRIORITAS_MAP[s.lower()]
    try:
        return int(float(s))
    except Exception:
        return default


def connect(db_path=None):
    return adb.connect(db_path)


def _now():
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS intentmap (
            id                   TEXT PRIMARY KEY,
            intent               TEXT NOT NULL,
            kategori             TEXT,
            struktur             TEXT,   -- mandiri/megaintent/bridging_child
            parent               TEXT,   -- untuk bridging_child: nama intent induk
            bridging             TEXT,   -- JSON array (anak/bridging di bawah megaintent)
            cakupan              TEXT,   -- JSON array (topik/utterance yang masuk sini)
            contoh_utterance     TEXT,   -- JSON array
            bukan_ini            TEXT,   -- JSON array (contoh negatif)
            alasan               TEXT,   -- maksud/rationale analis
            batasan_dialogflow   TEXT,   -- keterbatasan DF yang mendasari keputusan
            status               TEXT,   -- aktif/usang
            prioritas            INTEGER DEFAULT 0,
            terverifikasi        INTEGER DEFAULT 0,
            terakhir_diperbarui  TEXT,
            created_at           TEXT,
            updated_at           TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_im_intent   ON intentmap(intent);
        CREATE INDEX IF NOT EXISTS idx_im_kategori ON intentmap(kategori);
        CREATE INDEX IF NOT EXISTS idx_im_struktur ON intentmap(struktur);
        """
    )
    _cols = [r[1] for r in conn.execute("PRAGMA table_info(intentmap)").fetchall()]
    if "lang" not in _cols:
        conn.execute("ALTER TABLE intentmap ADD COLUMN lang TEXT DEFAULT 'id'")
    conn.commit()
    return conn


def _slug(s):
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s or "intent"


def _norm_lang(v):
    s = str(v or 'id').strip().lower()
    return 'en' if s in ('en', 'eng', 'english', 'inggris') else 'id'




def _norm_list(v):
    """Terima list ATAU string (dipisah baris) -> list string bersih.
    Catatan: pakai HANYA newline sebagai pemisah (bukan koma), karena cakupan/
    contoh utterance bisa mengandung koma."""
    if v is None:
        return []
    if isinstance(v, str):
        parts = v.split("\n")
    elif isinstance(v, (list, tuple)):
        parts = []
        for x in v:
            parts.extend(str(x).split("\n"))
    else:
        parts = [str(v)]
    out, seen = [], set()
    for p in parts:
        p = p.strip()
        k = p.lower()
        if p and k not in seen:
            seen.add(k)
            out.append(p)
    return out


def _to_int(v, default=0):
    try:
        return int(v)
    except Exception:
        try:
            return int(float(v))
        except Exception:
            return default


def _bool01(v):
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, (int, float)):
        return 1 if int(v) != 0 else 0
    s = str(v).strip().lower()
    return 1 if s in ("1", "true", "ya", "yes", "on", "verified") else 0


def _loads(s):
    if not s:
        return []
    try:
        v = json.loads(s)
        return v if isinstance(v, list) else []
    except Exception:
        return []


def row_to_dict(r):
    d = dict(r)
    for k in ("bridging", "cakupan", "contoh_utterance", "bukan_ini"):
        d[k] = _loads(d.get(k))
    d["terverifikasi"] = int(d.get("terverifikasi") or 0)
    d["prioritas"] = int(d.get("prioritas") or 0)
    d["prioritas_label"] = num_to_prioritas(d["prioritas"])
    d["lang"] = (d.get("lang") or "id")
    return d


def validate(data):
    """Kembalikan (ok, pesan). Fokus: intent + maksud analis wajib jelas."""
    intent = (data.get("intent") or "").strip()
    alasan = (data.get("alasan") or "").strip()
    if not intent:
        return False, "Field 'intent' (nama intent) wajib diisi."
    if len(intent) > 120:
        return False, "Nama intent terlalu panjang (maks 120 karakter)."
    if not alasan:
        return False, "Field 'alasan' (maksud analis) wajib diisi — inilah inti pengetahuan ini."
    if len(alasan) < 10:
        return False, "Alasan terlalu pendek; jelaskan kenapa analis memetakan begini."
    struktur = (data.get("struktur") or "mandiri").strip().lower()
    if struktur not in STRUKTUR:
        return False, "Struktur tidak valid. Pilih: " + ", ".join(STRUKTUR)
    if struktur == "bridging_child" and not (data.get("parent") or "").strip():
        return False, "Struktur 'bridging_child' wajib mengisi 'parent' (intent induk)."
    status = (data.get("status") or "aktif").strip().lower()
    if status not in STATUS:
        return False, "Status harus 'aktif' atau 'usang'."
    return True, ""


def upsert_intent(conn, data):
    ok, msg = validate(data)
    if not ok:
        raise ValueError(msg)
    intent = data["intent"].strip()
    iid = (data.get("id") or "").strip() or ("im_" + _slug(intent))
    now = _now()
    exists = conn.execute("SELECT created_at FROM intentmap WHERE id=?", (iid,)).fetchone()
    created = (exists["created_at"] if exists else now)
    row = (
        iid,
        intent,
        (data.get("kategori") or "").strip(),
        (data.get("struktur") or "mandiri").strip().lower(),
        (data.get("parent") or "").strip(),
        json.dumps(_norm_list(data.get("bridging")), ensure_ascii=False),
        json.dumps(_norm_list(data.get("cakupan")), ensure_ascii=False),
        json.dumps(_norm_list(data.get("contoh_utterance")), ensure_ascii=False),
        json.dumps(_norm_list(data.get("bukan_ini")), ensure_ascii=False),
        (data.get("alasan") or "").strip(),
        (data.get("batasan_dialogflow") or "").strip(),
        (data.get("status") or "aktif").strip().lower(),
        prioritas_to_num(data.get("prioritas")),
        _bool01(data.get("terverifikasi")),
        now[:10],
        created,
        now,
        _norm_lang(data.get("lang")),
    )
    conn.execute(
        """
        INSERT INTO intentmap
          (id, intent, kategori, struktur, parent, bridging, cakupan,
           contoh_utterance, bukan_ini, alasan, batasan_dialogflow, status,
           prioritas, terverifikasi, terakhir_diperbarui, created_at, updated_at, lang)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
           intent=excluded.intent, kategori=excluded.kategori,
           struktur=excluded.struktur, parent=excluded.parent,
           bridging=excluded.bridging, cakupan=excluded.cakupan,
           contoh_utterance=excluded.contoh_utterance, bukan_ini=excluded.bukan_ini,
           alasan=excluded.alasan, batasan_dialogflow=excluded.batasan_dialogflow,
           status=excluded.status, prioritas=excluded.prioritas,
           terverifikasi=excluded.terverifikasi,
           terakhir_diperbarui=excluded.terakhir_diperbarui,
           updated_at=excluded.updated_at, lang=excluded.lang
        """,
        row,
    )
    conn.commit()
    return {"ok": True, "id": iid, "created": not bool(exists)}


def get_intent(conn, iid):
    r = conn.execute("SELECT * FROM intentmap WHERE id=?", (iid,)).fetchone()
    return row_to_dict(r) if r else None


def delete_intent(conn, iid):
    cur = conn.execute("DELETE FROM intentmap WHERE id=?", (iid,))
    conn.commit()
    return cur.rowcount > 0


def count(conn):
    return conn.execute("SELECT COUNT(*) FROM intentmap").fetchone()[0]


def list_intents(conn, q=None, kategori=None, struktur=None, status=None, limit=2000, lang=None):
    sql = "SELECT * FROM intentmap"
    where, params = [], []
    if q:
        where.append("(LOWER(intent) LIKE ? OR LOWER(cakupan) LIKE ? OR LOWER(alasan) LIKE ?)")
        like = "%" + q.strip().lower() + "%"
        params += [like, like, like]
    if kategori:
        where.append("kategori=?"); params.append(kategori)
    if struktur:
        where.append("struktur=?"); params.append(struktur.lower())
    if status:
        where.append("status=?"); params.append(status.lower())
    if lang:
        where.append("lang=?"); params.append(str(lang).lower())
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY prioritas DESC, intent COLLATE NOCASE ASC LIMIT ?"
    params.append(_to_int(limit, 2000))
    rows = conn.execute(sql, params).fetchall()
    return [row_to_dict(r) for r in rows]


_STOP = set(
    "saya aku kami mau ingin gimana bagaimana cara kok ya dan atau di ke dari "
    "yang untuk apa apakah tolong min pak bu nya dong sih itu ini dulu lama sudah "
    "tidak gak ga tak lagi ada mohon bisa".split()
)


def _tokens(s):
    return [t for t in re.findall(r"[a-z0-9]+", (s or "").lower())
            if t not in _STOP and len(t) > 1]


def _key_hit(key, ql, qtok):
    """True bila 'key' (frasa cakupan/contoh) dianggap cocok dengan query.
    - substring langsung, ATAU
    - >=60% token penting frasa muncul di query (toleran variasi kata),
    - untuk frasa 1 token, token itu harus ada di query."""
    key = (key or "").strip().lower()
    if not key:
        return False
    if key in ql:
        return True
    ktok = _tokens(key)
    if not ktok:
        return False
    hit = sum(1 for t in ktok if t in qtok)
    if len(ktok) == 1:
        return hit == 1
    return (hit / len(ktok)) >= 0.6 and hit >= 2


def match(conn, query, limit=5):
    """Cari kebijakan intent relevan untuk sebuah utterance.
    Dipakai mesin analisis untuk menyuntik 'kunci jawaban' analis ke prompt.
    Cocok bila frasa cakupan/contoh muncul (persis atau mirip) di query, atau
    nama intent muncul.
    """
    ql = (query or "").lower()
    if not ql.strip():
        return []
    qtok = set(_tokens(query))
    rows = conn.execute(
        "SELECT * FROM intentmap WHERE status='aktif' ORDER BY prioritas DESC"
    ).fetchall()
    hasil = []
    for r in rows:
        d = row_to_dict(r)
        keys = list(d.get("cakupan") or []) + list(d.get("contoh_utterance") or [])
        if d.get("intent"):
            keys.append(d["intent"])
        if not any(_key_hit(k, ql, qtok) for k in keys):
            continue
        hasil.append(d)
        if len(hasil) >= limit:
            break
    return hasil


def build_context_text(matches, max_items=5):
    """Ubah hasil match() menjadi teks ringkas untuk disuntik ke system prompt."""
    if not matches:
        return ""
    lines = ["KEBIJAKAN PEMETAAN INTENT (maksud analis — pakai sebagai acuan benar):"]
    for m in matches[:max_items]:
        s = '- Intent "%s" (%s). Maksud: %s' % (
            m.get("intent", ""), m.get("struktur", "mandiri"), m.get("alasan", ""))
        lines.append(s)
        if m.get("struktur") == "megaintent" and m.get("bridging"):
            lines.append("  Bridging anakan: " + "; ".join(m["bridging"]))
        if m.get("struktur") == "bridging_child" and m.get("parent"):
            lines.append("  Berada di bawah megaintent: " + m["parent"])
        if m.get("bukan_ini"):
            lines.append("  BUKAN untuk: " + "; ".join(m["bukan_ini"]))
    return "\n".join(lines)


# --- Data contoh (seed) --------------------------------------------------
SEED = [
    {"intent": "Perubahan Data", "kategori": "Perubahan Data", "struktur": "mandiri",
     "prioritas": 90, "terverifikasi": 1, "status": "aktif",
     "cakupan": [
        "lupa email dan nomor telepon", "email dan no hp sudah tidak aktif",
        "ganti email", "ganti nomor hp", "ubah data kontak", "email lama tidak bisa diakses",
        "nomor hp hilang tidak bisa terima kode"],
     "contoh_utterance": ["saya lupa email dan no hp yang dulu", "email dan hp saya sudah tidak aktif"],
     "bukan_ini": ["lupa password tapi email masih aktif (-> reset mandiri)", "lupa EFIN (-> intent EFIN)"],
     "alasan": "User yang tidak tahu lagi email & no hp lamanya TIDAK bisa reset/verifikasi mandiri via chatbot, sehingga harus diarahkan ke jalur Perubahan Data (perlu bantuan petugas / update data kontak). Ini keputusan analis, bukan keluhan login biasa.",
     "batasan_dialogflow": "Chatbot tidak bisa memverifikasi identitas untuk reset otomatis bila kontak sudah tidak aktif."},

    {"intent": "UMKM", "kategori": "UMKM", "struktur": "megaintent",
     "prioritas": 90, "terverifikasi": 1, "status": "aktif",
     "bridging": ["UMKM - Pendaftaran", "UMKM - PPh Final 0,5%", "UMKM - Batas Omzet 4,8M", "UMKM - Cara Bayar"],
     "cakupan": ["umkm", "pajak umkm", "pph final umkm", "omzet umkm", "tarif 0.5 persen", "pp 55", "usaha kecil"],
     "contoh_utterance": ["pajak untuk umkm gimana", "tarif pph final umkm berapa"],
     "bukan_ini": ["PPh badan non-UMKM", "PPN (walau dari pelaku UMKM PKP)"],
     "alasan": "Materi UMKM sangat banyak. Bila dipecah menjadi banyak intent terpisah, training phrase saling tumpang tindih dan risiko fallback/MKTA naik. Analis memutuskan menampung semua ke SATU megaintent 'UMKM', lalu memandu ke sub-topik lewat bridging intent anakan.",
     "batasan_dialogflow": "Intent yang terlalu mirip menyebabkan salah-match & fallback tinggi; lebih stabil satu intent besar + bridging."},

    {"intent": "UMKM - PPh Final 0,5%", "kategori": "UMKM", "struktur": "bridging_child",
     "parent": "UMKM", "prioritas": 50, "terverifikasi": 1, "status": "aktif",
     "cakupan": ["tarif pph final umkm", "0,5 persen", "cara bayar pph final umkm", "jangka waktu pph final"],
     "contoh_utterance": ["berapa tarif pph final umkm", "sampai kapan bisa pakai tarif 0,5%"],
     "bukan_ini": ["pendaftaran UMKM (-> UMKM - Pendaftaran)"],
     "alasan": "Anak bridging dari megaintent UMKM khusus tarif PPh Final 0,5% (PP 55/2022). Dipisah sebagai bridging agar jawaban lebih terarah tanpa membuat intent top-level baru.",
     "batasan_dialogflow": "Dipanggil via follow-up/bridging dari intent UMKM, bukan sebagai intent mandiri."},

    {"intent": "UMKM - Pendaftaran", "kategori": "UMKM", "struktur": "bridging_child",
     "parent": "UMKM", "prioritas": 50, "terverifikasi": 1, "status": "aktif",
     "cakupan": ["daftar umkm", "cara daftar npwp umkm", "syarat umkm"],
     "contoh_utterance": ["cara daftar pajak umkm", "syarat jadi wajib pajak umkm"],
     "bukan_ini": ["tarif/bayar (-> UMKM - PPh Final 0,5% / UMKM - Cara Bayar)"],
     "alasan": "Anak bridging megaintent UMKM untuk topik pendaftaran/registrasi UMKM.",
     "batasan_dialogflow": "Bridging follow-up dari intent UMKM."},

    {"intent": "EFIN", "kategori": "Autentikasi", "struktur": "mandiri",
     "prioritas": 30, "terverifikasi": 0, "status": "aktif",
     "cakupan": ["efin", "lupa efin", "aktivasi efin", "minta efin"],
     "contoh_utterance": ["cara dapat efin", "lupa efin saya"],
     "bukan_ini": ["lupa email & no hp (-> Perubahan Data)", "lupa password (-> reset akun)"],
     "alasan": "[PERLU VERIFIKASI TIM] Semua urusan EFIN (lupa/aktivasi/reset) dikumpulkan di intent EFIN; cabang detailnya diatur di pustaka disambiguasi. Pastikan batas cakupan sesuai kebijakan tim terbaru.",
     "batasan_dialogflow": "Cabang lupa vs aktivasi vs reset ditangani lewat disambiguasi, bukan intent terpisah."},
]


def seed_defaults(conn, force=False):
    if force:
        conn.execute("DELETE FROM intentmap")
        conn.commit()
    n = 0
    for item in SEED:
        try:
            upsert_intent(conn, item)
            n += 1
        except Exception as e:
            print("seed intentmap gagal untuk", item.get("intent"), ":", e)
    return n


if __name__ == "__main__":
    c = init_db(connect())
    seeded = seed_defaults(c, force=True)
    print("intentmap rows:", count(c), "(seeded:", seeded, ")")
    m = match(c, "saya lupa email dan no hp yang lama gimana")
    print("match 'lupa email+hp':", [(x["intent"], x["struktur"]) for x in m])
    m2 = match(c, "tarif pph final umkm berapa ya")
    print("match 'umkm':", [(x["intent"], x["struktur"]) for x in m2])
    print("contoh konteks:\n" + build_context_text(m))


# ===== Katalog Intent: helper dasar (hashing, id, dekode baris) =====
import hashlib as _hashlib


def _cat_id(name, lang="id"):
    lg = str(lang or "id").strip().lower()
    if lg == "id":
        return "kat_" + _slug(name or "")
    return "kat_" + lg + "_" + _slug(name or "")


def _sha1(s):
    return _hashlib.sha1((s or "").encode("utf-8")).hexdigest()


def _hash_source(phrases, answer):
    parts = [str(p) for p in (phrases or [])]
    parts.append("||ANS||")
    parts.append(answer or "")
    return _sha1("\n".join(parts))


def sample_phrases(phrases, k=12):
    out = []
    for p in (phrases or []):
        s = str(p).strip()
        if s:
            out.append(s)
        if len(out) >= k:
            break
    return out


def _cat_has_desc(row):
    d = row if isinstance(row, dict) else dict(row)
    return bool((d.get("deskripsi_maksud") or "").strip() or (d.get("deskripsi_cakupan") or "").strip())


def _cat_row(r):
    d = dict(r)
    def _arr(v):
        if isinstance(v, list):
            return v
        try:
            x = json.loads(v) if v else []
            return x if isinstance(x, list) else []
        except Exception:
            return []
    d["sistem_tersinggung"] = _arr(d.get("sistem_tersinggung"))
    d["training_phrase_contoh"] = _arr(d.get("training_phrase_contoh"))
    for k in ("jumlah_training_phrase", "frekuensi_panggil", "terverifikasi",
              "perlu_deskripsi", "sumber_berubah"):
        d[k] = _to_int(d.get(k), 0)
    d["disetujui_oleh"] = d.get("disetujui_oleh") or ""
    d["disetujui_pada"] = d.get("disetujui_pada") or ""
    d["lang"] = (d.get("lang") or "id")
    d["soft_deleted"] = _to_int(d.get("soft_deleted"), 0)
    d["soft_deleted_at"] = d.get("soft_deleted_at") or ""
    d["soft_deleted_by"] = d.get("soft_deleted_by") or ""
    d["last_called_at"] = d.get("last_called_at") or ""
    return d


def init_catalog(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS intentmap_catalog ("
        "id TEXT PRIMARY KEY, intent TEXT NOT NULL, "
        "deskripsi_maksud TEXT DEFAULT '', deskripsi_cakupan TEXT DEFAULT '', "
        "sistem_tersinggung TEXT DEFAULT '[]', training_phrase_contoh TEXT DEFAULT '[]', "
        "jawaban_cuplikan TEXT DEFAULT '', jumlah_training_phrase INTEGER DEFAULT 0, "
        "frekuensi_panggil INTEGER DEFAULT 0, hash_sumber TEXT DEFAULT '', "
        "deskripsi_diperbarui TEXT DEFAULT '', sumber_deskripsi TEXT DEFAULT '', "
        "sumber_status TEXT DEFAULT 'aktif', terverifikasi INTEGER DEFAULT 0, "
        "perlu_deskripsi INTEGER DEFAULT 1, sumber_berubah INTEGER DEFAULT 0, "
        "disetujui_oleh TEXT DEFAULT '', disetujui_pada TEXT DEFAULT '', "
        "created_at TEXT, updated_at TEXT)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cat_perlu ON intentmap_catalog(perlu_deskripsi)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cat_freq ON intentmap_catalog(frekuensi_panggil)")
    _cols = [r[1] for r in conn.execute("PRAGMA table_info(intentmap_catalog)").fetchall()]
    if "disetujui_oleh" not in _cols:
        conn.execute("ALTER TABLE intentmap_catalog ADD COLUMN disetujui_oleh TEXT DEFAULT ''")
    if "disetujui_pada" not in _cols:
        conn.execute("ALTER TABLE intentmap_catalog ADD COLUMN disetujui_pada TEXT DEFAULT ''")
    if "lang" not in _cols:
        conn.execute("ALTER TABLE intentmap_catalog ADD COLUMN lang TEXT DEFAULT 'id'")
    # Epik E: kolom siklus-hidup intent (soft delete + tanggal terakhir dipanggil)
    for _lc_col, _lc_decl in (
        ("soft_deleted", "INTEGER DEFAULT 0"),
        ("soft_deleted_at", "TEXT DEFAULT ''"),
        ("soft_deleted_by", "TEXT DEFAULT ''"),
        ("last_called_at", "TEXT DEFAULT ''"),
    ):
        if _lc_col not in _cols:
            conn.execute("ALTER TABLE intentmap_catalog ADD COLUMN %s %s" % (_lc_col, _lc_decl))
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cat_softdel ON intentmap_catalog(soft_deleted)")
    conn.commit()
    return conn


def sync_catalog(conn, intents, freq_map=None, mark_missing=True):
    """Selaraskan katalog dgn data intent Dialogflow TANPA menimpa deskripsi.
    Return {total, baru, berubah, tetap, hilang}."""
    init_catalog(conn)
    freq_map = freq_map or {}
    now = _now()
    baru = berubah = tetap = hilang = total = 0
    seen_ids = set()
    for it in (intents or []):
        name = (it.get("intent") or it.get("display_name") or "").strip()
        if not name:
            continue
        lang = str(it.get("lang") or "id").strip().lower()
        if lang not in ("id", "en"):
            lang = "id"
        total += 1
        phrases = _norm_list(it.get("training_phrases") or it.get("phrases"))
        answer = (it.get("answer") or it.get("response_text") or "").strip()
        h = _hash_source(phrases, answer)
        freq = _to_int(freq_map.get(name, 0), 0)
        cid = _cat_id(name, lang)
        seen_ids.add(cid)
        sample = sample_phrases(phrases, 12)
        cuplik = answer if len(answer) <= 400 else (answer[:400] + " \u2026")
        existing = conn.execute("SELECT * FROM intentmap_catalog WHERE id=?", (cid,)).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO intentmap_catalog (id, intent, lang, training_phrase_contoh, "
                "jawaban_cuplikan, jumlah_training_phrase, frekuensi_panggil, hash_sumber, "
                "sumber_status, perlu_deskripsi, sumber_berubah, sumber_deskripsi, "
                "terverifikasi, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?, 'aktif', 1, 0, '', 0, ?, ?)",
                (cid, name, lang, json.dumps(sample, ensure_ascii=False), cuplik, len(phrases),
                 freq, h, now, now),
            )
            baru += 1
        else:
            ex = dict(existing)
            has_desc = _cat_has_desc(ex)
            changed = (ex.get("hash_sumber") or "") != h
            cols = ["intent=?", "lang=?", "training_phrase_contoh=?", "jawaban_cuplikan=?",
                    "jumlah_training_phrase=?", "frekuensi_panggil=?", "hash_sumber=?",
                    "sumber_status='aktif'", "updated_at=?"]
            vals = [name, lang, json.dumps(sample, ensure_ascii=False), cuplik, len(phrases),
                    freq, h, now]
            if changed and has_desc:
                cols.append("sumber_berubah=1")
                berubah += 1
            elif changed and not has_desc:
                cols.append("perlu_deskripsi=1")
                tetap += 1
            else:
                tetap += 1
            conn.execute("UPDATE intentmap_catalog SET " + ", ".join(cols) + " WHERE id=?",
                         tuple(vals) + (cid,))
    if mark_missing:
        rows = conn.execute("SELECT id FROM intentmap_catalog WHERE sumber_status='aktif'").fetchall()
        for r in rows:
            if r["id"] not in seen_ids:
                conn.execute("UPDATE intentmap_catalog SET sumber_status='hilang', updated_at=? WHERE id=?",
                             (now, r["id"]))
                hilang += 1
    conn.commit()
    return {"total": total, "baru": baru, "berubah": berubah, "tetap": tetap, "hilang": hilang}


def intents_needing_description(conn, limit=500, only_called=False):
    init_catalog(conn)
    where = ["perlu_deskripsi=1", "sumber_status='aktif'", "sumber_deskripsi=''"]
    if only_called:
        where.append("frekuensi_panggil>0")
    sql = ("SELECT * FROM intentmap_catalog WHERE " + " AND ".join(where) +
           " ORDER BY frekuensi_panggil DESC, intent COLLATE NOCASE ASC LIMIT ?")
    rows = conn.execute(sql, (_to_int(limit, 500),)).fetchall()
    return [_cat_row(r) for r in rows]


def save_ai_description(conn, iid, maksud, cakupan, sistem=None):
    init_catalog(conn)
    row = conn.execute("SELECT sumber_deskripsi FROM intentmap_catalog WHERE id=?", (iid,)).fetchone()
    if not row:
        return {"ok": False, "error": "tidak ditemukan"}
    if (row["sumber_deskripsi"] or "") == "analis":
        return {"ok": False, "locked": True}
    now = _now()
    if isinstance(sistem, str):
        sistem = [sistem]
    sistem_json = json.dumps([str(s).strip() for s in (sistem or []) if str(s).strip()],
                             ensure_ascii=False)
    conn.execute(
        "UPDATE intentmap_catalog SET deskripsi_maksud=?, deskripsi_cakupan=?, "
        "sistem_tersinggung=?, sumber_deskripsi='ai', terverifikasi=0, perlu_deskripsi=0, "
        "deskripsi_diperbarui=?, updated_at=? WHERE id=?",
        ((maksud or "").strip(), (cakupan or "").strip(), sistem_json, now, now, iid),
    )
    conn.commit()
    return {"ok": True}


def approve_description(conn, iid, edits=None, disetujui_oleh=None):
    init_catalog(conn)
    row = conn.execute("SELECT * FROM intentmap_catalog WHERE id=?", (iid,)).fetchone()
    if not row:
        return {"ok": False, "error": "tidak ditemukan"}
    edits = edits or {}
    now = _now()
    maksud = edits.get("deskripsi_maksud", row["deskripsi_maksud"])
    cakupan = edits.get("deskripsi_cakupan", row["deskripsi_cakupan"])
    if "sistem_tersinggung" in edits:
        sistem = json.dumps(_norm_list(edits.get("sistem_tersinggung")), ensure_ascii=False)
    else:
        sistem = row["sistem_tersinggung"] or "[]"
    approver = (disetujui_oleh or "").strip()
    conn.execute(
        "UPDATE intentmap_catalog SET deskripsi_maksud=?, deskripsi_cakupan=?, "
        "sistem_tersinggung=?, sumber_deskripsi='analis', terverifikasi=1, perlu_deskripsi=0, "
        "sumber_berubah=0, disetujui_oleh=?, disetujui_pada=?, deskripsi_diperbarui=?, updated_at=? WHERE id=?",
        ((maksud or "").strip(), (cakupan or "").strip(), sistem, approver, now, now, now, iid),
    )
    conn.commit()
    return {"ok": True, "id": iid, "disetujui_oleh": approver}


def get_by_intent(conn, name):
    init_catalog(conn)
    r = conn.execute("SELECT * FROM intentmap_catalog WHERE id=?", (_cat_id(name),)).fetchone()
    return _cat_row(r) if r else None


def catalog_stats(conn):
    init_catalog(conn)
    def c(where):
        return conn.execute("SELECT COUNT(*) FROM intentmap_catalog WHERE " + where).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM intentmap_catalog").fetchone()[0]
    return {
        "total_katalog": total,
        "perlu_deskripsi": c("sumber_deskripsi='' AND sumber_status='aktif'"),
        "draf_perlu_review": c("sumber_deskripsi='ai' AND terverifikasi=0"),
        "terverifikasi": c("terverifikasi=1"),
        "sumber_berubah": c("sumber_berubah=1"),
        "hilang": c("sumber_status='hilang'"),
    }


def catalog_list(conn, q=None, filt="all", limit=500, lang=None):
    init_catalog(conn)
    where, params = [], []
    f = (filt or "all").lower()
    if f == "perlu":
        where.append("sumber_deskripsi='ai' AND terverifikasi=0")
    elif f in ("review", "belum"):
        where.append("sumber_deskripsi='' AND sumber_status='aktif'")
    elif f == "berubah":
        where.append("sumber_berubah=1")
    elif f == "verified":
        where.append("terverifikasi=1")
    if q:
        where.append("(LOWER(intent) LIKE ? OR LOWER(deskripsi_maksud) LIKE ? OR LOWER(deskripsi_cakupan) LIKE ?)")
        like = "%" + q.strip().lower() + "%"
        params += [like, like, like]
    if lang:
        where.append("lang=?"); params.append(str(lang).lower())
    sql = "SELECT * FROM intentmap_catalog"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY frekuensi_panggil DESC, intent COLLATE NOCASE ASC LIMIT ?"
    params.append(_to_int(limit, 500))
    rows = conn.execute(sql, params).fetchall()
    return [_cat_row(r) for r in rows]


def match_catalog(conn, query, limit=4):
    init_catalog(conn)
    ql = (query or "").lower()
    if not ql.strip():
        return []
    qtok = set(_tokens(query))
    rows = conn.execute(
        "SELECT * FROM intentmap_catalog WHERE sumber_status='aktif' "
        "AND (deskripsi_maksud!='' OR deskripsi_cakupan!='') "
        "ORDER BY terverifikasi DESC, frekuensi_panggil DESC"
    ).fetchall()
    hasil = []
    for r in rows:
        d = _cat_row(r)
        keys = list(d.get("training_phrase_contoh") or [])
        if d.get("intent"):
            keys.append(d["intent"])
        if d.get("deskripsi_cakupan"):
            keys.append(d["deskripsi_cakupan"])
        if not any(_key_hit(k, ql, qtok) for k in keys):
            continue
        hasil.append(d)
        if len(hasil) >= limit:
            break
    return hasil


def build_catalog_context_text(matches, max_items=4):
    if not matches:
        return ""
    lines = ["KATALOG INTENT (deskripsi maksud user & cakupan jawaban - sebagian masih draf AI):"]
    for m in matches[:max_items]:
        draf = "" if int(m.get("terverifikasi") or 0) == 1 else " [DRAF - BELUM DIVERIFIKASI analis]"
        lines.append('- Intent "%s"%s' % (m.get("intent", ""), draf))
        if m.get("deskripsi_maksud"):
            lines.append("  Maksud user: " + m["deskripsi_maksud"])
        if m.get("deskripsi_cakupan"):
            lines.append("  Cakupan jawaban: " + m["deskripsi_cakupan"])
        sysx = m.get("sistem_tersinggung") or []
        if sysx:
            lines.append("  Sistem terkait: " + ", ".join(sysx))
    return "\n".join(lines)


# ===== Epik E: Manajemen Siklus-Hidup Intent (Data Terpakai) =====
# Sumber "terpanggil": tabel `interactions` (data Dialogflow) yang berada di DB
# yang sama dengan katalog. Intent dianggap terpanggil bila namanya muncul di
# interactions. "last_called_at" diturunkan dari MAX(day) (tanggal UTC).

def _lc_today(ref_date=None):
    if ref_date:
        return str(ref_date)[:10]
    return _dt.datetime.now(_dt.timezone.utc).date().isoformat()


def _lc_days_between(d1, d2):
    """Selisih hari dari d1 (lebih awal) ke d2. Format 'YYYY-MM-DD'. None bila gagal."""
    try:
        a = _dt.date.fromisoformat(str(d1)[:10])
        b = _dt.date.fromisoformat(str(d2)[:10])
        return (b - a).days
    except Exception:
        return None


def _lc_thr_days(retensi_bulan):
    try:
        return int(round(float(retensi_bulan) * 30.44))
    except Exception:
        return 183


def _has_interactions(conn):
    try:
        r = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='interactions'"
        ).fetchone()
        return bool(r)
    except Exception:
        return False


def refresh_lifecycle(conn):
    """Perbarui last_called_at & frekuensi_panggil katalog dari tabel interactions.
    Aman dipanggil walau tabel interactions belum ada (mis. sandbox kosong).
    Return {updated, no_interactions?}."""
    init_catalog(conn)
    if not _has_interactions(conn):
        return {"updated": 0, "no_interactions": True}
    rows = conn.execute(
        "SELECT intent_name AS nm, MAX(day) AS md, COUNT(*) AS c "
        "FROM interactions WHERE intent_name IS NOT NULL AND intent_name<>'' "
        "GROUP BY intent_name"
    ).fetchall()
    cmap = {}
    for r in rows:
        cmap[r["nm"]] = ((r["md"] or ""), _to_int(r["c"], 0))
    now = _now()
    updated = 0
    cat = conn.execute("SELECT id, intent FROM intentmap_catalog").fetchall()
    for r in cat:
        md, c = cmap.get(r["intent"], ("", 0))
        conn.execute(
            "UPDATE intentmap_catalog SET last_called_at=?, frekuensi_panggil=?, updated_at=? WHERE id=?",
            (md or "", c, now, r["id"]),
        )
        updated += 1
    conn.commit()
    return {"updated": updated}


def lifecycle_overview(conn, retensi_bulan=6, ref_date=None):
    """Ringkasan siklus-hidup: total, dipanggil, tidak dipanggil, soft-deleted,
    kandidat retensi (tidak dipanggil ATAU idle >= retensi_bulan, & belum soft-deleted)."""
    init_catalog(conn)
    today = _lc_today(ref_date)
    thr = _lc_thr_days(retensi_bulan)
    rows = conn.execute(
        "SELECT frekuensi_panggil, last_called_at, soft_deleted FROM intentmap_catalog"
    ).fetchall()
    total = dipanggil = tidak = soft = kandidat = 0
    for r in rows:
        total += 1
        sd = _to_int(r["soft_deleted"], 0)
        if sd:
            soft += 1
        lca = r["last_called_at"] or ""
        called = bool(lca)
        if called:
            dipanggil += 1
        else:
            tidak += 1
        if not sd:
            idle = _lc_days_between(lca, today) if lca else None
            if (not called) or (idle is not None and idle >= thr):
                kandidat += 1
    return {
        "total": total,
        "dipanggil": dipanggil,
        "tidak_dipanggil": tidak,
        "soft_deleted": soft,
        "kandidat_retensi": kandidat,
        "retensi_bulan": float(retensi_bulan),
        "retensi_hari": thr,
        "ref_date": today,
    }


def _lc_decorate(d, today, thr):
    lca = d.get("last_called_at") or ""
    called = bool(lca)
    sd = _to_int(d.get("soft_deleted"), 0)
    idle = _lc_days_between(lca, today) if lca else None
    retensi = (not sd) and ((not called) or (idle is not None and idle >= thr))
    d["dipanggil"] = called
    d["idle_hari"] = idle
    d["idle_bulan"] = (round(idle / 30.44, 1) if idle is not None else None)
    d["kandidat_retensi"] = retensi
    d["soft_deleted"] = sd
    if sd:
        d["siklus_status"] = "soft_deleted"
    elif not called:
        d["siklus_status"] = "tidak_dipanggil"
    elif retensi:
        d["siklus_status"] = "retensi"
    else:
        d["siklus_status"] = "aktif"
    return d


def lifecycle_list(conn, filt="all", q=None, limit=1000, retensi_bulan=6, ref_date=None, lang=None):
    """Daftar intent + status siklus-hidup. filt: all|dipanggil|tidak|retensi|softdeleted|aktif."""
    init_catalog(conn)
    today = _lc_today(ref_date)
    thr = _lc_thr_days(retensi_bulan)
    where, params = [], []
    if q:
        where.append("LOWER(intent) LIKE ?")
        params.append("%" + q.strip().lower() + "%")
    if lang:
        where.append("lang=?")
        params.append(str(lang).lower())
    sql = "SELECT * FROM intentmap_catalog"
    if where:
        sql += " WHERE " + " AND ".join(where)
    # urut: belum pernah dipanggil dulu, lalu paling lama tidak dipanggil
    sql += (" ORDER BY (last_called_at IS NULL OR last_called_at='') DESC, "
            "last_called_at ASC, frekuensi_panggil DESC, intent COLLATE NOCASE ASC")
    rows = conn.execute(sql, params).fetchall()
    f = (filt or "all").lower()
    lim = _to_int(limit, 1000)
    out = []
    for r in rows:
        d = _lc_decorate(_cat_row(r), today, thr)
        called = d["dipanggil"]
        sd = d["soft_deleted"]
        ret = d["kandidat_retensi"]
        if f == "dipanggil" and not called:
            continue
        if f in ("tidak", "tidak_dipanggil") and called:
            continue
        if f == "retensi" and not ret:
            continue
        if f in ("softdeleted", "soft_deleted") and not sd:
            continue
        if f == "aktif" and (sd or not called or ret):
            continue
        out.append(d)
        if len(out) >= lim:
            break
    return out


def set_soft_delete(conn, ident, deleted=True, user=None):
    """Tandai/pulihkan soft-delete satu intent. ident = id katalog ATAU nama intent."""
    init_catalog(conn)
    ident = (ident or "").strip()
    if not ident:
        return {"ok": False, "error": "id/intent kosong."}
    row = conn.execute("SELECT id FROM intentmap_catalog WHERE id=?", (ident,)).fetchone()
    if not row:
        row = conn.execute("SELECT id FROM intentmap_catalog WHERE intent=?", (ident,)).fetchone()
    if not row:
        row = conn.execute("SELECT id FROM intentmap_catalog WHERE id=?", (_cat_id(ident),)).fetchone()
    if not row:
        return {"ok": False, "error": "intent tidak ditemukan di katalog."}
    iid = row["id"]
    now = _now()
    val = 1 if deleted else 0
    conn.execute(
        "UPDATE intentmap_catalog SET soft_deleted=?, soft_deleted_at=?, soft_deleted_by=?, updated_at=? WHERE id=?",
        (val, (now if val else ""), ((user or "").strip() if val else ""), now, iid),
    )
    conn.commit()
    return {"ok": True, "id": iid, "soft_deleted": val}
