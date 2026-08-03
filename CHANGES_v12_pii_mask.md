# Camerad Studio — v12 (Masking PII, Fase A)

Tanggal: 2 Agu 2026

## Ringkasan
Menambahkan **masking PII (Fase A)** — perlindungan privasi berbasis regex ringan
(tanpa dependensi eksternal) yang menyamarkan data sensitif **sebelum** teks apa
pun dikirim ke LLM cloud (OpenAI/Gemini/Azure). Selaras dengan UU PDP dan
keputusan "data tidak bocor ke luar".

## Yang berubah

### Baru: `pii_mask.py`
Modul terpusat, stdlib murni (`re`). Fungsi utama:
- `mask_text(text) -> text` (alias `mask`) — mengganti PII dengan penanda.
- `masking_enabled()` — baca env `PII_MASKING` (default **on**).
- `scan(text)` — diagnostik jumlah temuan per jenis (tidak mengubah teks).

Pola yang dideteksi (khusus Indonesia):
| Jenis | Pola | Penanda |
|---|---|---|
| NIK / NPWP-baru | 16 digit polos | `<NIK>` |
| NPWP lama (polos) | 15 digit polos | `<NPWP>` |
| NPWP lama (berformat) | `99.999.999.9-999.999` | `<NPWP>` |
| Nomor HP | `08xx` / `+62xx` / `62xx` (10-14 digit) | `<HP>` |
| Email | `nama@domain.tld` | `<EMAIL>` |

Catatan: `1500200` (nomor layanan agent, 7 digit) **tidak** ikut ter-mask karena
tidak cocok pola NIK/NPWP/HP.

### Titik integrasi (sebelum kirim ke LLM)
- `studio_routes.py` → `_llm()`: `doc_text`, `question`, dan konteks global (`gctx`)
  di-mask; chunk map-reduce otomatis ikut ter-mask karena berasal dari `doc_text`.
- `web_app.py` → **Tanya AI & chat**:
  - `answer_knowledge_question()` — mask `question` + seluruh `system` (termasuk konteks halaman).
  - `answer_data_question()` — mask `question` (text-to-SQL) + mask `system` & payload hasil query (baris `interactions` bisa memuat PII).
  - `chat_llm()` (dipakai `/api/chat`) — mask semua `messages[].content` + `system`.

### Toggle
`PII_MASKING` (env). Default aktif. Set `PII_MASKING=off` (atau `0`/`false`/`no`)
untuk menonaktifkan sementara (mis. debugging).

```bash
# Windows PowerShell
$env:PII_MASKING = "off"; uvicorn web_app:app --host 0.0.0.0 --port 8080
# Linux/macOS
PII_MASKING=off uvicorn web_app:app --host 0.0.0.0 --port 8080
```

## Batasan (Fase A)
- Belum mendeteksi **nama orang / lokasi** (butuh NER — direncanakan Fase B/Presidio).
- Regex bersifat heuristik; angka 15/16 digit non-PII (mis. nomor invoice panjang)
  bisa ikut ter-mask. Bisa disempurnakan di Fase B dengan checksum.

## Fase B (rencana, belum dikerjakan)
Ganti "mesin" di balik `mask_text` dengan Microsoft/Data-Privacy-Stack **Presidio**
(`AnalyzerEngine` + `AnonymizerEngine`) + custom `PatternRecognizer` untuk
NIK/NPWP/HP dan NER `PERSON`/`LOCATION`. Titik integrasi TIDAK berubah.

## Uji
- `py_compile` web_app.py, studio_routes.py, docstudio.py, pii_mask.py → OK
- Smoke pii_mask: NIK 16, NPWP 15 & berformat, HP 08/+62, email → ter-mask;
  `1500200` tetap utuh; toggle off = teks utuh.
