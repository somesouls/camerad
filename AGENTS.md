# AGENTS.md - Panduan struktur & konvensi camerad

Dokumen ini untuk developer & AI agent yang menyentuh repo ini. Baca dulu sebelum menambah berkas atau memindah modul.

## Ringkasan
`camerad` adalah aplikasi FastAPI (chatbot + RAG peraturan pajak + Avaya AWE) yang dijalankan satu proses via `web_app.py`. Kode sudah ditata jadi paket Python per-domain (bukan lagi berkas datar di root).

## Peta paket (per-domain)
- `avaya/` - integrasi Avaya AWE (client, pipeline, dashboard/speed patch, db).
- `awe/` - analitik AWE (assess, deflection, overview, botfilter, routes).
- `chat/` - endpoint chat frontend & agent.
- `common/` - util lintas-domain (llm_client, text_norm, pii_mask, regref, intent_describe).
- `db/` - lapisan penyimpanan/koneksi SQLite per subsistem.
- `df_webhook/` - webhook Dialogflow.
- `evaluation/` - harness evaluasi, judge, sampler, sweep, dll.
- `handoff/` - perutean handoff (mandiri/agent/KPP).
- `knowledge/` - konteks pengetahuan, glossary, intentmap, disambig, stats.
- `peraturan/` - parser/semantic/embedding korpus peraturan.
- `pipeline/` - langkah pipeline + patch step9/step10.
- `pustaka/` - routes pustaka.
- `rag/` - mesin RAG (engine, router, reranker, rewrite) + patch runtime.
- `sop/`, `sosmed/` - SOP & sosial media.
- `routes/` - lapisan HTTP/menu (system, studio, lifecycle, analytics, auth, data, audit_tp, ...).
- `static/`, `templates/` - aset & presentasi (mis. `index.html`).

## Entry point (tetap di root - memang seharusnya)
`web_app.py` (aplikasi utama), `app_core.py`, `ingest.py`, `docstudio.py`, `phase0/1/2_upgrade.py`, `phase4_eval.py`, `phase5_qa_build.py`, `llm_fix_final_combined.py`. Berkas ini dijalankan langsung (`python web_app.py`), bukan diimpor sebagai paket.

## Kenapa dikelompokkan per-DOMAIN, bukan per-MENU?
- Satu modul backend melayani banyak menu, dan satu menu memakai banyak modul -> pengelompokan per-menu memaksa duplikasi & ketergantungan silang.
- Per-domain memberi kohesi tinggi & kopling rendah: perubahan satu fitur cenderung terlokalisir di satu paket.
- Sumbu "menu" tetap ada, di tempat yang tepat: `routes/` + `*_routes.py` (sumbu HTTP/menu) dan `templates/` (presentasi, mis. `index.html`). Organisasi berlapis: domain (logika) x routes (menu) x templates (tampilan).

## Konvensi
- Jangan tambah modul datar baru di root. Buat di paket domain yang sesuai (atau paket baru + `__init__.py`).
- Jangan bikin shim `sys.modules[__name__]` baru di root - CI akan menolaknya (lihat guard di bawah).
- Impor pakai jalur paket penuh, mis. `from rag.engine import ...`, `import avaya.pipeline as ...`.
- Konstanta path berbasis `__file__` di dalam paket: naikkan ke root repo dengan `os.path.dirname` bertingkat sesuai kedalaman paket.
- Rahasia (`.env`, `service-account.json`, `*.db`, `*.zip`) sudah di-`.gitignore`; jangan commit.

## Guard otomatis
- `scripts/oneoff/check_structure.py` - `py_compile` semua .py (tanpa deps pihak-ketiga) + menolak shim baru di root.
- CI GitHub Actions menjalankannya tiap push/PR (`.github/workflows/ci.yml`).
- Opsional lokal: `pre-commit install` (config di `.pre-commit-config.yaml`).

Detail arsitektur lengkap: `docs/ARCHITECTURE.md`.

---

## Konteks operasional & pemeliharaan RAG (untuk AI agent)

> Dirawat agar asisten tidak menanyakan hal yang sama berulang. Perbarui bila lingkungan/keputusan berubah. Terakhir diperbarui: 27 Agu 2026 (HEAD 55ff2f9).

### Lingkungan & deploy
- Repo `somesouls/camerad`, branch kerja `main`. Deploy: `chat.agenthebat.com` (satu proses `web_app.py`).
- Dev lokal: `C:\Users\USER\chatbot\pipeline_lokal`, virtualenv `.venv` (PowerShell). GPU NVIDIA RTX 5060 Ti. LLM: Azure `gpt-5.4-mini`.
- Model: embedding `BAAI/bge-m3` (1024-d), reranker `BAAI/bge-reranker-v2-m3`, intent `paraphrase-multilingual-mpnet-base-v2`.
- Basis data SQLite (di-`.gitignore`, TIDAK ada di repo): `peraturan.db`, `golden.db`, `evaluation.db`, log agent. Semua diagnostik/eval dijalankan di mesin dev/produksi, bukan dari repo.
- Alur perubahan: asisten push kode ke repo -> Fabel `git pull` + restart. Asisten TIDAK mengeksekusi kode di server; minta Fabel menjalankan perintah lalu menempelkan keluarannya.

### Tiga surface yang WAJIB tetap jalan
Mesin RAG harus menjawab benar di ketiganya (boleh lambat, TIDAK boleh nyangkut/loop):
1. Uji Cepat (lab) - `/api/rag/lab` -> `jawab_lab` (pakai profil `agent` tersimpan).
2. Chat Baru `/` - `/api/rag/agent`.
3. Live Chat `/livechat` - `/api/df/webhook` -> `jawab_chat`.

### Aturan mengikat (jangan dilanggar)
- JANGAN override/hardcode `sumber` dari profil; sumber efektif ditentukan checkbox profil.
- Bedah tipis (surgical), env-gated, fail-open. JANGAN rewrite berkas besar (`rag/engine.py` ~37KB, `peraturan/db.py` ~47KB, `avaya/pipeline.py`) - tambah patch kecil / panggil fungsi yang sudah ada.
- Satu berkas per push, pesan commit singkat, verifikasi readback (tail) setelah push; tanpa push paralel.
- Tiap tahap perubahan: uji (golden-set + 3 surface) sebelum lanjut. Sediakan rollback (umumnya set env terkait = 0).

### Perintah diagnostik (jalankan di `.venv`, dari root repo)
```powershell
# Status indeks peraturan (embedding vs unit, distribusi status, relasi, FTS)
python -c "import peraturan.db as p; print('STATS', p.stats()); print('FTS', p.fts_info())"

# Golden set: seed sekali, lalu evaluasi retrieval (recall@k / MRR / proksi abstain) + simpan baseline
python phase4_eval.py --seed
python phase4_eval.py --k 10 --baseline-save golden_base.json

# Kandidat golden dari feedback produksi (jempol-down / fallback)
python phase4_eval.py --mine

# Gerbang regresi sebelum upgrade berikutnya (exit 1 bila turun melebihi toleransi)
python phase4_eval.py --k 10 --baseline-check golden_base.json --tolerance 0.05
```

### Arsitektur retrieval (ringkas - SUDAH terpasang, bukan TODO)
- Peraturan: hybrid FTS5/BM25 (v3 ternormalisasi + kolom `nomor`) + vektor bge-m3 di-fusi RRF (k=60), lalu filter `status IN ('berlaku')`; dibungkus rewrite (kamus+AI) + cross-encoder rerank. Lihat `peraturan/db.py::search`, `rag/rerank_patch.py`.
- Intent: leksikal + semantik di-fusi RRF + rerank (`rag/engine.py::_ctx_dialogflow`).
- Q2Q AWE/Sosmed + tautan pasal + penanda referensi dicabut/diubah: `rag/qa_patch.py`.
- Tata kelola masa berlaku (mekanisme sudah ada, gap ada di DATA): kolom `status/valid_from/valid_to/dicabut_oleh/diubah_oleh`, `bulk_update_status`, `peraturan_relasi` + `trace_successor()`.
- Rantai patch produksi (urutan diimpor `web_app.py` & `phase4_eval.py`): successor -> rerank -> kalibrasi -> domain.

### Env penting (default) & rollback
Rollback = set knob terkait ke `0`. Utama: `RAG_ROUTER2=1`, `RAG_RERANK=1` (`RAG_RERANK_POOL=30`), `RAG_MIN_COS=0.650`, `PERATURAN_FLOOR_SKOR=0.010`, `RAG_QA_PATCH=1` (`RAG_QA_MIN_COS=0.50`, `RAG_QA_TOPK=3`), `RAG_INTENT_SEMANTIC=1`, `PERATURAN_EMBED=1`, `RAG_GPU_GATE=1`, `SOSMED_INDEX=1`, `AWE_INDEX=1` (`AWE_INDEX_LIMIT=100000`). Daftar & contoh penuh: `.env.example`.

### Baseline indeks (27 Agu 2026, HEAD 55ff2f9)
- `total_unit=33868` = `total_vec=33868` (embedding 100%); `total_pasal=32614`; `total_lampiran_unit=1023`; `total_peraturan=2237`.
- Status: berlaku 19437 / dicabut 7580 / diubah 6851 (42,6% sudah bertanda tidak-berlaku). `total_relasi=3499`. `unit_bertag=29152` (86%). Triase: ok 3359, perlu_ocr 248, kosong 41.
- FTS versi 3 (target 3), `fts_rows=33868`, normalisasi + stemming Sastrawi aktif.
- Implikasi: indeks & embedding SUDAH sinkron penuh (tak perlu rebuild/reindex). Fokus perbaikan = tata kelola DATA masa berlaku + cakupan kamus/ekspansi kueri, bukan infrastruktur retrieval.
