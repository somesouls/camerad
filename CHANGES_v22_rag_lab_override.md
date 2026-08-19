# CHANGES v22 — Hotfix: signature guardrail (error 'override') + hapus halaman /rag-lab

## Latar

Dua temuan setelah restart (19 Agu 2026):

1. **Error** `_guarded_answer() got an unexpected keyword argument 'override'`
   di form uji /rag-chatbot & /rag-agent. Akar masalah: penulisan ulang
   `rag_grounding_patch` (v18) memakai signature pembungkus yang TIDAK sama
   dengan `rag_engine.answer` asli — `(question, profile, override=None,
   history=None, diagnostics=False, honor_mode=False)`. Semua pemanggil
   (`jawab_chat`, `jawab_lab`, eval harness) mengirim kwarg tersebut ->
   TypeError di semua jalur jawaban.
2. Halaman **/rag-lab** sudah tidak dipakai (form uji sudah dipisah ke
   /rag-chatbot & /rag-agent) namun route-nya masih terdaftar.

## Perubahan

- **`rag_grounding_patch.py`**: signature pembungkus disamakan PERSIS dengan
  `rag_engine.answer`; seluruh kwarg diteruskan apa adanya; fallback TypeError
  tetap fail-open. Guardrail (tautan tak resmi, anti-karang-pasal, abstain
  tanpa sumber) tidak berubah.
- **`rag_routes.py`**: route halaman `/rag-lab` DIHAPUS. API `/api/rag/lab`
  DIPERTAHANKAN — ia adalah backend form "Uji Cepat" di /rag-chatbot &
  /rag-agent (terbukti dari kedua template). Menu sidebar memang sudah bersih
  dari /rag-lab.
- **`templates/rag_lab.html`** dihapus (yatim; tak ada yang merujuk).

## Penerapan

`git pull origin main`, lalu restart `web_app.py`. Tanpa migrasi.
