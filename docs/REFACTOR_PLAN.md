# Rencana Refactor Struktur Repo `camerad`

> Status: rancangan. Langkah 1 (pembersihan + rencana) dieksekusi bersama dokumen ini.
> Tujuan: merapikan struktur repo **tanpa mengubah perilaku runtime**.

## 0. Prinsip

1. **Behavior-preserving.** "Pindah file" TIDAK boleh digabung dengan "ubah arsitektur" (mis. melebur patch ke modulnya). Satu jenis perubahan per PR.
2. **Bertahap, PR kecil, per-cluster.** Naik dari risiko nol → tinggi. Merge hanya setelah smoke test hijau.
3. **LoRA tidak butuh refactor ini.** Fine-tuning bisa ditambah di `training/` tanpa menyentuh runtime. Jangan jadikan refactor sebagai gerbang untuk mulai fine-tuning.
4. **Setiap langkah wajib lulus smoke test banner** (lihat §5).

## 1. Temuan wiring (audit `web_app.py` & `app_core.py`)

`web_app.py` adalah **hub import** dan menerapkan patch pada **urutan yang KRITIS** (banyak komentar eksplisit "WAJIB diimpor SETELAH ..."). Mengubah urutan atau lokasi berkas bisa mematahkan patch **secara diam-diam, tanpa error**.

### JANGAN pindahkan — patch runtime (di-apply saat import di `web_app.py`)
`step9_patch`, `step10_patch`, `rag_successor_patch`, `rag_rerank_patch`, `rag_calibration_patch`, `rag_domain_patch`, `rag_drilldown_patch`, `rag_grounding_patch`, `awe_botfilter_patch`, `handoff_routing_patch`, `rag_sources_patch`, `rag_qa_patch`.

> ⚠️ `step9_patch.py` & `step10_patch.py` **terlihat** seperti "skrip step", tetapi sebenarnya **patch runtime** (mem-patch `pipeline_routes.step9_save` / `step10_build`). BUKAN skrip sekali-jalan — jangan dipindah ke `scripts/`.

### JANGAN pindahkan — entrypoint / fondasi
- `web_app.py` — entrypoint UI (uvicorn 8080).
- `llm_fix_final_combined.py` — **server backend** di `127.0.0.1:8000` (dipanggil via HTTP), bukan skrip sekali-jalan.
- `app_core.py` — fondasi bersama. **`BASE_DIR = folder berkas ini`**; `templates/`, `static/`, `service-account.json`, `_runs/` semua dihitung relatif ke lokasi `app_core.py`. Jika `app_core.py` dipindah ke subfolder, semua path itu rusak kecuali `BASE_DIR` dijangkar ulang ke root proyek.

### Jebakan `sys.path` untuk skrip CLI
Menjalankan `python scripts/phaseX.py` menaruh **`scripts/`** (bukan root) di `sys.path[0]`, sehingga `import rag_engine` GAGAL. Skrip yang dipindah ke subfolder HARUS dijalankan sebagai modul dari root (`python -m scripts.phaseX`) **atau** menyisipkan root ke `sys.path` di barisnya.

## 2. Klasifikasi berkas root

| Kelompok | Contoh | Tujuan | Risiko |
| --- | --- | --- | --- |
| Modul domain (imported runtime) | `rag_*`, `peraturan_*`, `sop_*`, `sosmed_*`, `awe_*`, `avaya_*`, `pipeline_*`, `eval_*`, `handoff_*`, `df_*`, `knowledge_*`, `intent*`, `*_db` | package per-domain | Sedang–Tinggi (perbaiki import + audit path) |
| Skrip CLI aktif | `phase0/1/2_upgrade.py`, `phase4_eval.py`, `phase5_qa_build.py` | `scripts/` (+ fix `sys.path`) | Rendah |
| Skrip sekali-jalan (arsip) | `fix_*.py`, `reset_verdict_mkta.py`, `step10_build_new.py`(?) | `scripts/oneoff/` SETELAH grep-guard | Rendah bila terbukti tak di-import |
| Dokumen | `CHANGES_*.md`, `*.txt`, `PANDUAN_*.md` | `docs/` (README.md tetap root) | Nol |
| Sampah | `*.bak`, `studio_routes copy.py`, `__pycache__` (ter-track) | hapus / untrack | Nol |

> Belum terklasifikasi (perlu grep-guard sebelum dipindah): `fix_*` mungkin di-import oleh modul rute (mis. `pipeline_routes`, `awe_*`). Verifikasi dulu.

## 3. Target struktur (usulan)

```
camerad/
  web_app.py                 # entrypoint UI (TETAP root)
  app_core.py                # fondasi (TETAP root; atau core/ dgn PROJECT_ROOT)
  llm_fix_final_combined.py  # backend 127.0.0.1:8000 (TETAP root)
  rag/        # engine, config, router, rewrite, reranker, intent, kamus, + SEMUA *_patch
  peraturan/
  sop/
  sosmed/
  awe/
  avaya/
  pipeline/   # + step9_patch, step10_patch (patch untuk pipeline)
  eval/
  chat/       # chat_frontend, df_webhook, handoff, agent_chat
  knowledge/
  common/     # llm_client, users_db, pii_mask, text_norm, regref, ...
  scripts/    # phase*, dan oneoff/
  docs/       # CHANGES_*, panduan
  templates/  static/        # TETAP
```

> Patch tetap sebagai berkas terpisah **di dalam** package-nya. JANGAN dilebur ke modulnya pada tahap ini (itu perubahan arsitektur, bukan pemindahan).

## 4. Urutan PR (tangga risiko)

1. **PR-1 (ini): pembersihan + rencana.** Hapus `*.bak` & `studio_routes copy.py`; tambah `*.bak` ke `.gitignore`; tambah `docs/REFACTOR_PLAN.md` + `scripts/reorg_docs.sh`. Runtime tak tersentuh.
2. **PR-2: pindah dokumen.** Jalankan `scripts/reorg_docs.sh` (pakai `git mv`, riwayat terjaga) → `CHANGES_*`/`*.txt`/panduan ke `docs/`, untrack `__pycache__`. Risiko nol.
3. **PR-3: pindah skrip.** `phase*` & `fix_*`/`reset_*` → `scripts/` + fix `sys.path`. `step9_patch`/`step10_patch`/`llm_fix_final_combined` **TETAP**. Grep-guard dulu.
4. **PR-4..N: package modul non-patch per-cluster.** Urutan aktual (risiko naik): **`sop/` (pilot — SELESAI)** → **`sosmed/` (berjalan)** → `awe/` (patch tetap root) → `eval/` → `peraturan/` (coupling tertinggi, terakhir dari kelompok ini). Tiap PR: buat package + `__init__.py` + shim mundur + audit path (`__file__`) + smoke test. Alat generik: **`scripts/reorg_pkg.sh`** (lihat §7).
5. **PR terakhir (paling hati-hati): `rag/` + lapisan patch.** Pertahankan urutan import di `web_app.py` PERSIS. Uji banner lengkap. Ini titik paling rawan (identitas modul + urutan patch).

## 5. Jaring pengaman

### A. Smoke test banner (murah, tajam)
Setelah tiap PR, `python web_app.py` HARUS mencetak semua baris ini. Hilang satu = wiring rusak:
```
[step9_patch] ...
[step10_patch] ...
[rag_successor_patch] ...
[rag_rerank_patch] ... device=cuda
[rag_calibration_patch] ...
[rag_domain_patch] ...
[rag_drilldown_patch] ...
[rag_grounding_patch] ...
[awe_botfilter_patch] ...
[handoff_routing_patch] ...
[rag_sources_patch] ...
[rag_qa_patch] ... (indeks Q&A: N vektor)
[peraturan_semantic] ... dim=1024
[rag_intent_semantic] ...
[text_norm] ...
[warmup] indeks semantik intent siap.
[warmup] model reranker cross-encoder siap.
[warmup] matriks vektor peraturan siap.
[warmup] matriks indeks Q&A siap.
[scheduler] ingest harian aktif ...
```

### B. Uji fungsional cepat
Buka `/`; kirim 1 pertanyaan di `/livechat` (harus **1** webhook, bukan badai — lihat v31); buka `/rag-agent`; jalankan 1 langkah pipeline.

### C. Pola shim (hindari big-bang)
Saat memindah modul yang di-import banyak tempat, tinggalkan berkas lama sebagai penerus agar tak ada "dua objek modul":
```python
# rag_engine.py (shim transisi)
from rag.engine import *            # noqa: F401,F403
from rag.engine import _DISPATCH, _ctx_peraturan, answer  # nama privat yang dipakai patch
```

### D. Jebakan identitas modul
Setelah pindah, pastikan tak ada campuran `import rag_engine` (lama) dengan `from rag import engine` (baru). Semua harus menunjuk objek modul yang SAMA — terutama modul yang di-patch, kalau tidak patch nempel di objek yang salah.

### E. Audit path sebelum memindah modul apa pun
Cari `__file__`, `os.path.join(BASE_DIR, ...)`, `open("....db")`, `"templates/"`, `"static/"`. Jangkar ulang ke root proyek bila perlu.

## 6. Catatan LoRA (terpisah dari refactor)

- Tambahkan `training/` (pembuat dataset + skrip PEFT) tanpa menyentuh runtime.
- LoRA baik untuk **gaya/format/kepatuhan instruksi**, buruk untuk **fakta** (cepat basi, tak bisa disitasi, menaikkan halusinasi) — biarkan RAG tetap penyuplai fakta + sumber.
- PEFT yang Anda latih sendiri butuh **model open-weight** lokal; `gpt-5.4-mini` di Azure tidak bisa ditempeli adapter Anda.

## 7. Catatan eksekusi & pelajaran (PR-3 / PR-4)

### 7.1 Alat generik `scripts/reorg_pkg.sh`
Pemaket cluster yang dipakai ulang tiap PR modul:
```
bash scripts/reorg_pkg.sh <pkg> <mod1> <mod2> ...
# contoh: bash scripts/reorg_pkg.sh sosmed db ingest knowledge routes x
```
- `git mv <pkg>_<mod>.py -> <pkg>/<mod>.py` (byte-exact, riwayat terjaga) + buat `<pkg>/__init__.py`.
- Shim mundur di root: `<pkg>_<mod>.py` mengalias `sys.modules[__name__]` ke `<pkg>.<mod>`.
- **Auto-deteksi patch `_BASE_DIR`:** bila file memuat PERSIS `_BASE_DIR = os.path.dirname(os.path.abspath(__file__))` (1x) dan itu SATU-SATUNYA `__file__`, dinaikkan satu level. Bila ada `__file__` di luar pola itu -> **ABORT + rollback** (wajib tinjau manual; jangan rusak path diam-diam).
- Commit LOKAL saja (belum push). Gerbang `py_compile` best-effort.

### 7.2 Pola shim final
Root `<pkg>_<mod>.py`:
```python
import sys as _sys
import <pkg>.<mod> as _mod
_sys.modules[__name__] = _mod
```
Satu objek modul (hindari duplikasi state/cache). `import <pkg>_<mod>` & `from <pkg>_<mod> import ...` tetap sah tanpa mengubah pemanggil (termasuk `web_app.py` & lapisan patch seperti `rag_sources_patch`).

### 7.3 Lingkungan Windows: DUA git & DUA python (sumber bug utama)
Mesin dev memakai **PowerShell** (venv; `python` ADA) dan **bash/WSL** (git terpisah; `python` TIDAK ADA, tapi `python3` ADA):
1. **`fatal: empty ident name`** saat commit dari bash -> identitas git global tak kebaca di bash. Obat: **repo-local** `git config user.name` + `user.email` (tanpa `--global`).
2. **PowerShell `git status` BERSIH tapi skrip-bash bilang KOTOR** -> beda `core.autocrlf`/`core.fileMode`. Obat: **repo-local** `git config core.autocrlf true` + `core.fileMode false`. `reorg_pkg.sh` menyetel ini otomatis di awal.
3. **`bash: python: command not found`** -> `python` cuma di venv PowerShell. Obat: skrip patch pakai **`sed`** (bukan python); `py_compile` best-effort (coba `python3`/`python`/`py`).
4. **PowerShell menolak `&&`** (`The token '&&' is not a valid statement separator`) -> rangkai perintah pakai `bash -c "... && ..."` atau `;`.
5. **CRLF pada `.sh`** -> `set -o pipefail` gagal. Obat: self-heal `tr -d '\r'` di baris atas + `.gitattributes` (`*.sh eol=lf`).
6. **`git clean -fd` terlalu luas** (pernah membuang `golden_baseline.json`, `venv/Include/`). SELALU scoped.

### 7.4 Gerbang wajib tiap cluster
`py_compile` HANYA cek sintaks — TIDAK menangkap import/path rusak. **Boot-test PowerShell (banner §5) + cek endpoint stats cluster (angka SAMA) tetap gerbang utama.** Rollback commit lokal bila perlu: `bash -c 'git reset --hard HEAD~1 && rm -rf <pkg>'`.

### 7.5 Progres PR-4
- **`sop/`** — pilot, SELESAI & terverifikasi (byte-exact; `_BASE_DIR` di `db.py` & `batch.py` dinaikkan; boot bersih).
- **`sosmed/`** — dikerjakan via `reorg_pkg.sh` (patch `_BASE_DIR` hanya di `db.py`; 4 modul lain tanpa `__file__`).
