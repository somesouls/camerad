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
