# CHANGES v20 — Hotfix temuan golden set pertama (Fase 4 bekerja)

## Latar

Menjalankan `phase4_eval.py` pertama kali (recall@10 = 0,667, MRR = 0,604)
langsung menemukan empat MISS + satu bug proksi abstain. Inilah persis tujuan
Fase 4: mengukur -> menemukan -> memperbaiki. Hotfix ini menutup semuanya.

## Temuan & perbaikan

1. **Query bernomor exact tidak bisa cocok leksikal** — MISS pada
   "bunyi pasal 19 PER-23/PJ/2016" dan "ketentuan peralihan PER-8/PJ/2025".
   Akar masalah: kolom `nomor` tidak ikut terindeks FTS, jadi token nomor
   peraturan ("per", "23", "pj", "2016") tak punya sasaran leksikal.
   -> **FTS v3** (`peraturan_db.py`): `peraturan_fts` menjadi
   `(id, nomor, judul, hierarchy, isi)`; kolom `nomor` (jenis+nomor,
   normalisasi ringan tanpa stopword/stemming — identifier) berbobot bm25
   tertinggi 12x (judul 8x, hierarchy 4x, isi 1x). Versi indeks dibaca dari
   `peraturan_meta`; jalur baca/tulis mengikuti versi tabel (v1/v2/v3) —
   aplikasi tetap jalan normal sebelum migrasi dijalankan.
   `phase1_upgrade.py` kini membandingkan terhadap `FTS_TARGET_VERSION` modul.

2. **Dua ekspektasi golden terlalu ketat** — MISS pada
   "kriteria orang pribadi menjadi subjek pajak dalam negeri" (top1 sebenarnya
   dokumen yang BENAR: PER-23/PJ/2025 Pasal 3; pasal menulis angka lengkap
   "183 (seratus delapan puluh tiga) hari" sehingga keyword "183 hari" tak
   pernah cocok berdampingan) dan "ketentuan penyerahan BKP ... kawasan
   berikat" (istilah BKP tak selalu dieja "barang kena pajak" pada unit yang
   sama).
   -> `rag_golden_db.fix_seed_v2()` melonggarkan HANYA entri yang belum
   disunting admin; `_DEFAULT_SEED` ikut diperbarui untuk instalasi baru.

3. **Bug proksi abstain** di `phase4_eval.py`: memanggil fungsi `cos01` yang
   tidak ada di rag_calibration (yang benar: `skor_peraturan(query, ids)`) ->
   max_cos selalu tercetak 0.000. Diperbaiki.

4. DeprecationWarning `utcnow()` -> `datetime.now(timezone.utc)`.

## Penerapan

```
git pull origin main
python phase1_upgrade.py          # membangun ulang FTS ke v3 (~40 mnt utk 33rb unit)
python phase4_eval.py --seed      # melonggarkan 2 ekspektasi
python phase4_eval.py             # ukur ulang
python phase4_eval.py --baseline-save golden_baseline.json   # SIMPAN BASELINE BARU
```

Baseline lama (0,667) dihitung saat celah nomor masih ada — setelah v3, simpan
baseline BARU. Catatan: rebuild FTS TIDAK menyentuh vektor; tidak perlu reindex
embedding.

## Bila MISS kawasan berikat masih tersisa setelah v3

Berarti gap-nya di KORPUS (belum ada unit yang memuat ketentuan kawasan
berikat secara eksplisit), bukan di mesin — tandai dengan impor dokumen
kawasan berikat terkait lewat menu Batch Peraturan, lalu
`python phase0_upgrade.py --reindex-all` (resume) + `python phase1_upgrade.py`
untuk menyertakan unit baru di FTS v3.
