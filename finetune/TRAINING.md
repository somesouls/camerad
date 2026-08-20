# Training QLoRA + Serving vLLM (WSL2)

Alur: **build dataset -> train bertahap (Unsloth QLoRA) -> serve (vLLM) -> daftar provider `local`**.

> **PENTING (Windows):** vLLM **tidak didukung Windows native** — hanya Linux.
> RTX 5060 Ti (Blackwell sm_120) juga butuh CUDA 12.8. Jalur yang didukung penuh
> untuk training **dan** serving adalah **WSL2 Ubuntu** dengan GPU passthrough.
> Jangan pasang torch/vllm ke `.venv` aplikasi — versinya bentrok & bisa merusak app.

## 0. Prasyarat di WSL2 (RTX 5060 Ti 16GB, Blackwell sm_120)

1. Driver NVIDIA Windows terbaru terpasang (WSL CUDA passthrough otomatis).
   Di WSL2 cek: `nvidia-smi` harus menampilkan RTX 5060 Ti.

2. **Python:** jangan andalkan python bawaan WSL — bisa terlalu baru (mis. 3.14)
   sehingga belum ada wheel torch/vllm, dan sering tak punya `venv`/`pip`.
   Pakai **Miniconda** untuk Python 3.12 yang didukung, **tanpa perlu sudo**:

```bash
cd ~
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash Miniconda3-latest-Linux-x86_64.sh      # Enter -> yes -> Enter -> yes (init)
# tutup lalu buka lagi terminal WSL; prompt harus muncul awalan (base)
conda create -n train python=3.12 -y
conda activate train
```

3. Install stack (torch cu128 dulu; WAJIB `<2.12` untuk Unsloth):

```bash
pip install "torch<2.12" torchvision --index-url https://download.pytorch.org/whl/cu128
pip install unsloth unsloth_zoo "trl>=0.9" "transformers>=4.44" "datasets>=2.20" \
            "peft>=0.12" "accelerate>=0.33" "bitsandbytes>=0.43.3" vllm
```

4. Cek torch benar-benar melihat GPU (harus muncul `...+cu128 True`):

```bash
python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
```

> Kalau `False`: torch-nya CPU-only (salah index). Ulangi `pip install torch`
> dengan `--index-url https://download.pytorch.org/whl/cu128`. JANGAN `pip install torch`
> polos — itu ambil wheel CPU dari PyPI dan Unsloth akan error
> "cannot find any torch accelerator".

## 1. Build dataset (tanpa GPU)

```bash
cd /mnt/c/Users/USER/chatbot/pipeline_lokal
python -m finetune.build_all
```

Hasil di `_runs/finetune/`: `intent.jsonl`, `faq.jsonl`, `grounded.jsonl` (+ `.val.jsonl`) + `manifest.json`.

> **Sudah pernah build di Windows?** File `_runs/finetune/*.jsonl` bisa dipakai ulang
> dari WSL2 asalkan dijalankan dari folder repo yang sama (mis. lewat `/mnt/c/...`).
> Tak perlu build ulang; langsung ke langkah 2.

## 2. Training bertahap (kumulatif)

Data terbersih dulu, lalu menumpuk perilaku di atasnya:

```bash
python -m finetune.train_qlora --staged
```

Setara dengan:

```bash
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

## 3. Serving vLLM (di WSL2)

```bash
python -m finetune.serve_vllm --print   # lihat perintah + adapter terdeteksi
python -m finetune.serve_vllm           # jalankan
```

Otomatis mendaftarkan tiap adapter di `_runs/finetune/adapters/*` sebagai LoRA
module bernama `camerad-<dataset>` (mis. `camerad-grounded`).

## 4. Daftar sebagai provider `local` (di .env aplikasi)

Aplikasi tetap jalan di Windows; dia cukup bicara ke vLLM di WSL2 lewat HTTP.
`localhost` di Windows sudah diforward ke WSL2 secara default:

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

## Troubleshooting

- **`ensurepip is not available` / `apt install python3.x-venv` / `pip not found`** ->
  python sistem WSL kurang lengkap / terlalu baru. Jangan pakai `python3 -m venv`
  sistem; pakai Miniconda + `conda create python=3.12` (langkah 0). Tak perlu sudo.
- **`Unsloth cannot find any torch accelerator? You need a GPU.`** -> torch CPU-only.
  Install ulang torch dari index cu128 (lihat langkah 0).
- **vllm error/aneh saat `serve` di PowerShell** -> vLLM tidak jalan di Windows
  native. Serve dari WSL2.
- **Bentrok versi torch dgn Unsloth** -> Unsloth butuh `torch<2.12`. Jangan pakai
  nightly `2.12.dev` milik `.venv` aplikasi; pakai cu128 stabil `<2.12`.
