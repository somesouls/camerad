# Camerad Studio — Perubahan v4: Filter Bahasa (lang id/en)

Menambahkan dimensi **bahasa (lang: `id` / `en`)** pada keempat pustaka dan Dashboard.
Data Dialogflow hanya berisi Bahasa Indonesia (`id`) dan English (`en`).

## 1. Skema database (migrasi otomatis, aman untuk DB lama)
Kolom `lang TEXT DEFAULT 'id'` ditambahkan lewat migrasi `ALTER TABLE ... ADD COLUMN`
di dalam `init_db` / `init_catalog` — tidak menyentuh blok `CREATE TABLE`, sehingga
DB lama tetap terbaca dan baris lama otomatis dianggap `id`.
- `glossary_db.py` → tabel `glossary`
- `disambig_db.py` → tabel `disambig`
- `intentmap_db.py` → tabel `intentmap` (kurasi) dan `intentmap_catalog` (katalog)

## 2. Katalog: satu intent, dua bahasa = dua baris terpisah
`_cat_id(name, lang)` sekarang lang-aware: `id` → `kat_<slug>`, `en` → `kat_en_<slug>`.
`sync_catalog` menyimpan/perbarui baris per-bahasa. Penarikan Dialogflow di menu
**Kelola Data** kini mengambil intent `languageCode=id` DAN `languageCode=en`
(masing-masing baris katalog diberi label bahasanya).

## 3. API list (filter `lang`)
`list_terms`, `list_rules`, `list_intents`, `catalog_list` menerima parameter
opsional `lang` (ditaruh setelah `limit`, tanpa memutus pemanggil lama).
Endpoint web menerima query `?lang=id|en` (kosong = semua bahasa).

## 4. Dashboard / Statistik
`analytics_db` (overview, top_intents, volume_by_day, new_questions, hot_topics)
menerima `lang` dan memfilter kolom `lang` pada tabel `interactions`.
Dashboard punya dropdown **Semua / ID / EN**.

## 5. UI (Glosarium, Disambiguasi, Intent Map + Katalog)
- Dropdown filter bahasa **Semua / ID / EN** di toolbar tiap daftar.
- Badge **`ID`/`EN`** biru di tiap baris (di samping badge "dipakai N×").
- Field **Bahasa (ID/EN)** di form tambah/edit; default `id`.

## Catatan (di luar cakupan / pekerjaan lanjutan)
Pencocokan chatbot (`knowledge_ctx.py`) **sengaja tidak diubah** — fitur ini murni
pengelolaan + tampilan + penyimpanan berbasis bahasa. Baris katalog EN baru muncul
setelah melakukan penarikan ulang Dialogflow (Step-3) di menu Kelola Data.
