# Camerad Studio — v8 (Fase 4: Epik B — “Tanya AI” di semua menu)

Fase 4 menghadirkan panel **Tanya AI kontekstual** di seluruh menu, sehingga analis bisa bertanya langsung dari halaman mana pun. Jawaban **dibatasi hanya dari database internal** (tidak ada sumber luar/web), dengan penyedia konteks per-halaman.

## Ringkasan perilaku
Satu endpoint terpusat `POST /api/ask` dengan body `{question, page, lang?}`. Perilaku ditentukan oleh halaman asal:

| Kelompok | Halaman | Mode | Sumber jawaban |
|---|---|---|---|
| Pustaka | Glosarium, Disambiguasi, Peta Intent | **knowledge** (berpagar) | Glosarium / Disambiguasi / Peta Intent & Katalog |
| Data | Data, Analisis Dialogflow, Deflection, Siklus Hidup | **data** (text-to-SQL read-only) | tabel `interactions` (sama seperti Dashboard) |
| Dashboard | — | (punya panel “AI Data Assistant” sendiri) | — |

> Dashboard sudah memiliki panel Tanya AI khusus (text-to-SQL), jadi panel global **tidak** ditambahkan di sana agar tidak dobel.

## Guardrail (batasan jawaban)
Mode **knowledge** memakai system-prompt berpagar:
- Jawab **HANYA** dari konteks internal yang disuplai.
- **Dilarang** memakai pengetahuan umum/eksternal, mencari web, atau menebak.
- Jika info tidak ada di konteks → wajib menjawab jujur: *“Maaf, informasi itu belum tersedia di data internal untuk halaman ini.”*

Mode **data** memakai jalur text-to-SQL yang sudah ada (`answer_data_question`) yang bersifat **read-only** dan sudah diinstruksikan untuk tidak mengarang di luar hasil query.

## Penyedia konteks per-halaman
`build_page_context(page, question, lang)` di `web_app.py` mengambil potongan data dari database masing-masing pustaka:
- **Glosarium**: `glossary_db.match()` (entri paling relevan); bila tak ada yang cocok, sertakan total istilah + contoh daftar istilah.
- **Disambiguasi**: `disambig_db.match()`; fallback ke daftar pemicu + total aturan.
- **Peta Intent**: gabungan `intentmap_db.match()` (kebijakan) + `match_catalog()` (deskripsi katalog).

Konteks silang lintas-pustaka tetap disuntik via `knowledge_ctx.system_suffix()` agar jawaban konsisten antar-menu.

## Backend — `web_app.py`
- Konstanta `ASK_DATA_PAGES`, `ASK_KNOWLEDGE_PAGES`, `ASK_GUARDRAIL`.
- `build_page_context(...)` — penyedia konteks per-halaman (aman bila DB kosong/bermasalah; kegagalan ditangani diam-diam).
- `answer_knowledge_question(page, question, lang)` — rakit prompt berpagar + panggil `llm_client.chat` (temperatur rendah 0.1).
- `POST /api/ask` — rute baru; knowledge-page → jawaban berpagar, selain itu → text-to-SQL. Menyertakan `mode` (`knowledge`/`data`) di respons.

## Frontend — `templates/base.html`
- Panel **Tanya AI** global (tombol mengambang kanan-bawah + panel chat) yang muncul otomatis di semua halaman **kecuali** Dashboard dan landing (`{% if active_page and active_page not in ['dashboard',''] %}`).
- Panel membaca `active_page` (via `data-page`), menampilkan label cakupan per-halaman, contoh pertanyaan kontekstual, dan me-render jawaban (Markdown ringan; untuk mode data juga tabel hasil + SQL referensi).
- Menempel gaya tema yang ada (`--accent`, `--panel-bg`, `--line`, `--radius`, `--shadow-float`, `--c-blue`), JS murni vanilla, tanpa Jinja di dalam `<script>`.

## Validasi
- `py_compile web_app.py`: OK
- Parse Jinja **12** template: OK
- Render `base.html`: panel muncul untuk `glosarium`/`data`, tersembunyi untuk `dashboard` & landing: OK
- `node --check` JS panel baru: OK (tanpa Jinja di script)
- Smoke pustaka (alur `build_page_context`): Glosarium match + fallback daftar OK; Disambiguasi fallback (kunci `pemicu`) OK; Peta Intent `match`/`match_catalog` OK.

> FastAPI tidak terpasang di sandbox, jadi rute `/api/ask` diverifikasi via py_compile + smoke fungsi pustaka pendukung. Verifikasi runtime akhir: jalankan `uvicorn web_app:app --host 0.0.0.0 --port 8080`, buka menu mana pun (mis. Glosarium atau Data), klik tombol **Tanya AI** di kanan-bawah.

## Cara pakai
1. Buka menu apa pun (selain Dashboard).
2. Klik tombol **Tanya AI** (mengambang, kanan-bawah).
3. Ajukan pertanyaan — di halaman pustaka AI menjawab dari pustaka terkait; di halaman data AI menjalankan query SQL read-only. Bila info tak ada, AI mengaku jujur alih-alih mengarang.
