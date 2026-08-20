# finetune/ — LoRA (QLoRA) untuk camerad

Paket ini menyiapkan **dataset fine-tuning LoRA** dari knowledge base yang sudah
ada, plus panduan training & serving. Tujuan: membangun profil **LLM chatbot**
dan **LLM agent** berbasis LoRA untuk dibandingkan dengan 2 profil **RAG** yang
sudah ada (RAG chatbot & RAG agent).

## Prinsip: LoRA = perilaku, RAG = fakta
- **LoRA (bobot)** mengajarkan *cara/gaya* menjawab, keputusan intent, disiplin
  sitasi, kapan bertanya balik. Cocok untuk hal yang stabil.
- **RAG (retrieval)** menyediakan *fakta* yang sering berubah (peraturan/SOP).
  Jangan menghafal pasal ke bobot — biarkan tetap di RAG.

## Tiga dataset (format chat terpadu)
Semua sampel: `{"messages":[{role,content}...], "meta":{...}}` (OpenAI/ShareGPT),
langsung dipakai Unsloth/Axolotl dan identik dengan skema serving vLLM.

1. **intent.jsonl** — klasifikasi intent (`build_intent.py`).
   Sumber: `knowledge.intentmap_db` (peta intent analis + katalog training phrase
   Dialogflow). Kandidat LoRA pertama terbaik: bersih & berlabel.
2. **faq.jsonl** — FAQ multi-turn (`build_faq.py`).
   Sumber: `db.qa_index_db` (sosmed X + livechat AWE). Satu utas = satu sampel
   multi-turn → mengajarkan follow-up & klarifikasi. PII di-mask.
3. **grounded.jsonl** — RAG-grounded (`build_grounded.py`).
   Sumber: `peraturan.db` + `sop.db`. Pola `{pertanyaan + konteks} → {jawaban +
   sitasi}` supaya model disiplin menjawab DARI konteks, bukan mengarang.

### Cara membangun
```
python -m finetune.build_all
# atau satu per satu
python -m finetune.build_intent
python -m finetune.build_faq
python -m finetune.build_grounded
```
Output ke `_runs/finetune/*.jsonl` (+ `.val.jsonl`, `manifest.json`).
Prasyarat: DB terisi (`peraturan.db`, `sop.db`, `qa.db`, `intentmap`) dan venv
Windows aktif bila ingin PII mask/model embedding aktif (opsional).

## Base model (rekomendasi)
**Qwen/Qwen2.5-7B-Instruct** — Indonesia kuat, reasoning + function-calling
(untuk profil agent), QLoRA-friendly di 16GB. Alternatif Indonesia-first:
SEA-LION v3 / Sahabat-AI (bisa di-A/B belakangan). Override via env
`FINETUNE_BASE_MODEL`.

## Training (QLoRA, RTX 5060 Ti 16GB / Blackwell sm_120)
- Pakai **Unsloth** (tercepat, hemat VRAM) atau Axolotl. 4-bit QLoRA, LoRA rank
  16–32, chat template sesuai base model, **loss di token assistant saja**.
- Blackwell: butuh CUDA 12.8+/torch cu128 (sudah terpasang). PIN versi terbaru
  yang mendukung sm_120: `bitsandbytes`, `unsloth`, `flash-attn`.
- Urutan bertahap: intent → +faq → +grounded (validasi tiap tahap).

## Serving (vLLM, OpenAI-compatible)
```
vllm serve Qwen/Qwen2.5-7B-Instruct --enable-lora \
  --lora-modules camerad=/path/adapter --port 8001
```
Daftarkan sebagai provider lokal di `common/` (mis. `LLM_PROVIDER=local`,
base `http://127.0.0.1:8001/v1`). Profil RAG lama tak berubah; profil LoRA
diarahkan ke endpoint ini.

## Menu perbandingan RAG vs LoRA (roadmap)
Tambah profil **LoRA chatbot** & **LoRA agent** di samping 2 profil RAG, lalu:
- **Live side-by-side**: satu pertanyaan → RAG-only / LoRA-only / LoRA+RAG.
- **Batch scorecard**: golden set via paket `evaluation/` → groundedness/sitasi,
  akurasi intent, halusinasi, fallback rate, latency, biaya.

## TODO (peningkatan mutu)
- Generator pertanyaan grounded berbasis LLM (`common.llm_client`) menggantikan
  template.
- Tautkan pertanyaan historis (regref di `qa.db`) ke unit peraturan untuk sampel
  grounded yang lebih natural.
