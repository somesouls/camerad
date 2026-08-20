# CHANGES v19 — Fase 4: Golden set + gerbang evaluasi retrieval

## Ringkasan

Menutup **Fase 4** dari rencana audit: evaluasi & loop kualitas berkelanjutan.
Infrastruktur `/rag-eval` yang ada (LLM-judge, sweep kalibrasi, hold-out AWE)
dibiarkan utuh — Fase 4 menambah yang belum ada: **golden set terkurasi dengan
rujukan yang diharapkan** dan **metrik retrieval deterministik** yang bisa
dijalankan kapan saja tanpa LLM, plus **penambangan feedback produksi** sebagai
kandidat golden set baru.

## Perubahan

### 1. `rag_golden_db.py` (baru) — penyimpanan golden set
- Tabel `rag_golden`: query, `jenis_harapan` (`hit`/`abstain`), `expect_json`
  (ekspektasi fleksibel: daftar `nomor` — salah satu cocok — dan/atau
  `keywords` — semua muncul dalam satu baris), catatan, `aktif`.
- Seed bawaan **20 query terkurasi** (12 hit + 8 abstain), termasuk dua query
  acuan audit (SPLN; BKP ke kawasan berikat) dan kasus "cara mengajukan SPLN di
  coretax" yang memang harus abstain. Seeding **merge idempoten** — tidak
  menimpa suntingan admin.
- `mirror_to_eval()` — mencerminkan entri aktif ke `eval_sample`
  (jenis=`golden`) sehingga ikut dinilai LLM-judge di `/rag-eval`.
- `mine_feedback()` — menambang kandidat golden set dari log produksi
  (jempol-down / jawaban fallback di `agent_log_db`).

### 2. `phase4_eval.py` (baru) — evaluasi retrieval deterministik (tanpa LLM)
```
python phase4_eval.py --seed           # isi golden set + cermin ke /rag-eval
python phase4_eval.py                  # jalankan recall@k + MRR + proksi abstain
python phase4_eval.py --baseline-save baseline.json   # simpan patokan
python phase4_eval.py --baseline-check baseline.json  # gerbang regresi (exit 1 bila turun)
python phase4_eval.py --mine           # kandidat baru dari feedback produksi
```
- Mengimpor patch retrieval (successor → rerank → kalibrasi → domain) dengan
  urutan yang sama seperti `web_app.py`, jadi yang diukur = rantai produksi.
- Laporan per-query (hit/rank/rujukan yang cocok) + agregat recall@k & MRR;
  untuk entri abstain dilaporkan cosine teratas sebagai proksi (bukan keputusan
  LLM — penilaian abstain penuh tetap lewat `/rag-eval` jenis=golden).

### 3. `.env.example` — `PIPELINE_GOLDEN_DB_FILE`.

## Cara pakai yang dianjurkan (loop kualitas)

1. `python phase4_eval.py --seed` (sekali)
2. Baseline awal: `python phase4_eval.py --baseline-save golden_baseline.json`
3. Sebelum upgrade model/perubahan retrieval apa pun:
   `python phase4_eval.py --baseline-check golden_baseline.json`
4. Penilaian end-to-end berkala (dengan LLM-judge): `/rag-eval` →
   `POST /api/eval/run {"profil":"agent","jenis":"golden","judge":true}`
   (atau pilih jenis golden bila sudah tampil di UI).
5. Mingguan: `python phase4_eval.py --mine` → kurasi pertanyaan bermasalah →
   tambahkan ke golden set.

## Rollback

Aditif murni (file baru + satu variabel env terdokumentasi). Hapus `golden.db`
dan dua file baru untuk meniadakan; jalur produksi tidak berubah.
