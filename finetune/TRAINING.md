# Training QLoRA + Serving vLLM

Alur: **build dataset -> train bertahap (Unsloth QLoRA) -> serve (vLLM) -> daftar provider `local`**.

## 0. Prasyarat GPU (RTX 5060 Ti 16GB, Blackwell sm_120)

Blackwell butuh build CUDA 12.8+. torch cu128 sudah terpasang. Pin paket berikut
saat menyiapkan environment training (idealnya venv terpisah dari app):

```
pip install "torch" --index-url https://download.pytorch.org/whl/cu128   # sudah ada
pip install unsloth unsloth_zoo
pip install "trl>=0.9" "transformers>=4.44" "datasets>=2.20" "peft>=0.12" "accelerate>=0.33"
pip install "bitsandbytes>=0.43.3"   # perlu build yang mendukung sm_120
# vLLM untuk serving (butuh wheel yang mendukung Blackwell/cu128):
pip install "vllm>=0.6.2"
```

> Catatan Blackwell: jika `bitsandbytes` / `flash-attn` / `vllm` prebuilt belum
> mendukung sm_120, pakai wheel nightly/cu128 atau build dari source. QLoRA 4-bit
> butuh bitsandbytes yang benar; kalau bermasalah, sementara pakai LoRA 16-bit
> (`FINETUNE_LOAD_IN_4BIT=0`) dengan max_seq_len lebih kecil.

## 1. Build dataset (tanpa GPU)

```
python -m finetune.build_all
```

Hasil di `_runs/finetune/`: `intent.jsonl`, `faq.jsonl`, `grounded.jsonl` (+ `.val.jsonl`) + `manifest.json`.

## 2. Training bertahap (kumulatif)

Data terbersih dulu, lalu menumpuk perilaku di atasnya:

```
python -m finetune.train_qlora --staged
```

Setara dengan:

```
python -m finetune.train_qlora --dataset intent
python -m finetune.train_qlora --dataset faq      --resume-from _runs/finetune/adapters/intent
python -m finetune.train_qlora --dataset grounded --resume-from _runs/finetune/adapters/faq
```

- Loss hanya pada token asisten (`train_on_responses_only`).
- Hyperparameter di `finetune/train_config.py`, semua bisa dioverride via env
  (mis. `FINETUNE_LORA_R=16`, `FINETUNE_EPOCHS_GROUNDED=1.5`).
- Adapter tersimpan di `_runs/finetune/adapters/<dataset>/`.

Untuk perbandingan RAG vs LoRA, kamu bisa latih **dua varian**:
- `intent`+`faq` saja  -> profil **LoRA chatbot** (gaya jawab + FAQ + follow-up).
- +`grounded`          -> profil **LoRA agent** (disiplin sitasi peraturan/SOP).

## 3. Serving vLLM

```
python -m finetune.serve_vllm --print   # lihat perintah + adapter terdeteksi
python -m finetune.serve_vllm           # jalankan
```

Otomatis mendaftarkan tiap adapter di `_runs/finetune/adapters/*` sebagai LoRA
module bernama `camerad-<dataset>` (mis. `camerad-grounded`).

## 4. Daftar sebagai provider `local` (di .env)

```
LLM_PROVIDER=local
VLLM_BASE_URL=http://127.0.0.1:8001/v1
VLLM_MODEL=camerad-grounded    # base 'Qwen/Qwen2.5-7B-Instruct' utk tanpa LoRA
```

`common/llm_client.py` sudah mengenali `LLM_PROVIDER=local` dan bicara ke vLLM
lewat jalur OpenAI-compatible (fungsi `chat()` / `generate()` tetap sama).

## 5. Berikutnya: menu banding RAG vs LoRA (Fase 4)

- Tambah profil **LoRA chatbot** & **LoRA agent** di samping 2 profil RAG.
- Mode live side-by-side: RAG-only / LoRA-only / LoRA+RAG.
- Batch scorecard via `evaluation/`: groundedness/sitasi, akurasi intent,
  halusinasi, fallback rate, latency, biaya.
