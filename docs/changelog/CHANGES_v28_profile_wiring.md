# CHANGES v28 — Wiring pengaturan halaman Konfigurasi Profil ↔ backend

## Latar

Keluhan pengguna (19 Agu 2026): "banyak pengaturan yang sudah diatur di
halaman tetapi tidak nyambung ke backend" + "kenapa harus melalui .env?".

Hasil audit wiring halaman Konfigurasi Profil (Agent & Chatbot):

| Pengaturan | Tersimpan | Dibaca mesin | Status |
| --- | --- | --- | --- |
| Sumber Pengetahuan | ✅ | ✅ effective_sources | nyambung |
| Maks. pertanyaan/hari | ✅ | ✅ kuota 429 | nyambung |
| **Maks. token / jawaban** | ✅ | ❌ hardcode 800 | **MATI → dihidupkan** |
| Putaran verifikasi (loop) | ✅ | ✅ answer() | nyambung |
| Suhu | ✅ | ✅ temperature | nyambung |
| Tampilkan tautan sumber | ✅ | ✅ kontrol sources | nyambung |
| Prompt & fallback | ✅ | ✅ render + guardrail | nyambung |
| **Mode mesin** | ✅ kolom `mode` ada & `save_profile` sudah menerimanya | ✅ `_resolve_mode` | **UI hilang → ditambahkan** |

## Perubahan

1. **`rag_engine.py`** — fungsi baru `_maks_token_for(profile)`: membaca
   `maks_token` dari kuota profil (agent_log_db; 0/kosong = default 800) dan
   memakainya sebagai `max_new_tokens` sintesis. Lazy import + fail-soft.
2. **`templates/rag_agent.html` & `templates/rag_chatbot.html`** — kartu
   "Parameter Mesin" kini punya selektor **Mode mesin** (Otomatis / Hemat LLM
   / Pipeline penuh / Tanpa LLM) yang menyimpan ke kolom `mode` profil yang
   memang sudah dibaca mesin. Dengan ini pengaturan kecepatan tidak perlu
   lewat `.env` lagi: pilih **Hemat LLM** untuk 1 panggil LLM per jawaban
   (tanpa AI-rewrite & loop verifikasi) — tersimpan per profil dan langsung
   berlaku untuk chat produksi (tanpa restart).

## Catatan pemakaian

- Form "Uji Cepat" dengan **Mode produksi TIDAK dicentang** sengaja memaksa
  pipeline penuh (diagnostik) — centang "Mode produksi" bila ingin merasakan
  mode nyata profil (mis. Hemat LLM).
- Daftar mode di halaman **Webhook Chatbot** menulis kolom yang sama untuk
  profil chatbot; dua-duanya konsisten.

## Penerapan

`git pull origin main` → restart `web_app.py`. Tanpa migrasi.