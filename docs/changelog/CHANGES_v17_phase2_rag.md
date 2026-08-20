# CHANGES v17 — Fase 2: Legal intelligence (relasi multi-hop + tagging + sinyal domain)

## Ringkasan

Menutup gap **P2** dari audit RAG Profil Agent: metadata hukum yang sudah
tersimpan di schema (`kekuatan_hukum`, `status_terkait`, `valid_from/to`)
sebelumnya **tidak ikut menentukan ranking**, relasi aturan hanya JSON blob
1-lompatan, dan bagian PENJELASAN belum terindeks sebagai unit tersendiri.
Fase 2 mengaktifkannya — aditif dan fail-soft (semua bisa dimatikan via env).

## Perubahan

### 1. `peraturan_db.py` — schema & fungsi legal intelligence
- Kolom baru `peraturan_unit`: `topik` (JSON), `entitas` (JSON), `jenis_unit`
  (batang_tubuh/penjelasan/lampiran) — migrasi lunak via ALTER TABLE.
- Tabel baru `peraturan_relasi` (`from_source -> to_source`, jenis
  `penerus`/`pendahulu`, tanggal, nomor/judul/link tujuan).
- `build_relasi()` — mengisi tabel relasi dari `status_terkait` /
  `history_terkait` (idempoten, INSERT OR IGNORE).
- `trace_successor(source_id, maks_lompatan=3)` — penelusuran rantai penerus
  multi-hop sampai dokumen berstatus `berlaku`.
- `tag_unit()` / `backfill_tags()` — pengisian entitas/topik
  dictionary-driven (kamus_sinonim + taksonomi topik bawaan: PPN, PPh, KUP,
  Kepabeanan, PBB/BPHTB, Bea Meterai, Sanksi & Penagihan, Insentif & Fasilitas).
  Auto-tag juga berjalan di `upsert_peraturan` (impor baru otomatis tertag).
- `stats()` kini ikut melaporkan `total_relasi` & `unit_bertag`.

### 2. `peraturan_parser.py` — dukungan bagian PENJELASAN
- Parser mengenali header `PENJELASAN ...` (termasuk bentuk huruf berjarak
  `P E N J E L A S A N`).
- Unit penjelasan: `jenis_unit='penjelasan'`, hierarchy berprefix
  `PENJELASAN > ...`, id berakhiran `-penj-pX` (tidak bentrok dengan batang
  tubuh pasal yang sama).
- Catatan: berlaku untuk dokumen yang DIIMPOR ULANG setelah v17.

### 3. `rag_successor_patch.py` (v2) — penelusuran pengganti multi-hop
- Bila `peraturan_relasi` terisi: rantai A(dicabut) -> B(diubah) -> C(berlaku)
  ditelusuri sampai ujung; penarikan ISI diprioritaskan dari dokumen ujung
  yang berlaku; catatan memuat rantai perubahan berurutan.
- Bila tabel relasi kosong: fallback ke jalur JSON 1-lompatan (perilaku v1).

### 4. `rag_domain_patch.py` (baru) — sinyal domain di ranking + filter temporal
- Skor akhir = `(1-alpha)*base_ternormalisasi + alpha*domain_score`;
  alpha via `RAG_DOMAIN_ALPHA` (default 0.25).
- Komponen domain: **authority** (`kekuatan_hukum`, penalti `can_cite=0`),
  **recency** (tahun), **entitas** (irisan istilah query vs `entitas` unit),
  **definisi** (query "apa itu/pengertian" -> boost unit memuat "yang dimaksud"
  / Pasal 1).
- **Filter temporal as-of**: query bertahun ("... tahun 2019") -> unit difilter
  `valid_from <= tgl <= valid_to` (NULL fail-open; bila filter mengosongkan,
  hasil asli dipakai).
- Membungkus rantai terakhir `peraturan_db.search` (gate -> rerank -> hybrid);
  gagal-anggun penuh. Kill-switch: `RAG_DOMAIN_BOOST=0`.

### 5. `phase2_upgrade.py` (baru) — eksekutor satu perintah
```
python phase2_upgrade.py
```
Membangun relasi + backfill tagging. Tanpa GPU/model/internet; idempoten.

### 6. `web_app.py` — impor `rag_domain_patch` (setelah `rag_calibration_patch`).
### 7. `.env.example` — blok env Fase 2 (`RAG_DOMAIN_ALPHA`, `RAG_W_*`).

## Urutan penerapan

1. `git pull origin main`
2. Restart aplikasi (`web_app.py`) — cari log `[rag_domain_patch] domain boost
   aktif ...` dan `[rag_successor_patch] ... (multi-hop bila peraturan_relasi terisi)`.
3. `python phase2_upgrade.py` — isi relasi + tagging (beberapa menit).
4. Uji `/rag-lab`: query bertahun ("aturan PPN ... tahun 2019"), query definisi
   ("apa itu subjek pajak luar negeri"), query rantai aturan yang dicabut.
5. Opsional (indeks bagian PENJELASAN): impor ulang dokumen lewat menu Batch
   Peraturan, lalu `python phase0_upgrade.py --reindex-all` (resume).

## Rollback
- `RAG_DOMAIN_BOOST=0` mematikan domain boost.
- Relasi/tagging bersifat aditif; menghapus isi tabel `peraturan_relasi` atau
  kolom tag tidak mengganggu jalur lama.
