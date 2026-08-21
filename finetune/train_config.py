# -*- coding: utf-8 -*-
"""
Konfigurasi training QLoRA (Unsloth) untuk camerad.

Semua nilai bisa dioverride lewat environment variable (prefix FINETUNE_*).
Tidak meng-import dependency berat (torch/unsloth) di level modul, supaya aman
di CI (py_compile) dan bisa dibaca skrip lain tanpa GPU.
"""
import os


def _env(name, default):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def _int(name, default):
    try:
        return int(_env(name, default))
    except (TypeError, ValueError):
        return int(default)


def _float(name, default):
    try:
        return float(_env(name, default))
    except (TypeError, ValueError):
        return float(default)


def _bool(name, default):
    v = _env(name, None)
    if v is None:
        return default
    return str(v).strip().lower() not in ("0", "false", "no", "off", "")


# Base model (samakan dengan finetune.common.DEFAULT_BASE_MODEL)
BASE_MODEL = _env("FINETUNE_BASE_MODEL", "Qwen/Qwen2.5-7B-Instruct")

# LoRA / QLoRA
LORA_R = _int("FINETUNE_LORA_R", 32)
LORA_ALPHA = _int("FINETUNE_LORA_ALPHA", 32)
# Dropout dinaikkan dari 0.0 -> 0.05 untuk mengurangi overfit (grounded sempat
# tembus loss ~0.004 -> output kolaps). Override via FINETUNE_LORA_DROPOUT.
LORA_DROPOUT = _float("FINETUNE_LORA_DROPOUT", 0.05)
TARGET_MODULES = [
    "q_proj", "k_proj", "v_proj", "o_proj",
    "gate_proj", "up_proj", "down_proj",
]
LOAD_IN_4BIT = _bool("FINETUNE_LOAD_IN_4BIT", True)
MAX_SEQ_LEN = _int("FINETUNE_MAX_SEQ_LEN", 2048)

# Optimizer / schedule
LEARNING_RATE = _float("FINETUNE_LR", 2e-4)
BATCH_SIZE = _int("FINETUNE_BATCH_SIZE", 2)
GRAD_ACCUM = _int("FINETUNE_GRAD_ACCUM", 8)
WARMUP_RATIO = _float("FINETUNE_WARMUP_RATIO", 0.03)
WEIGHT_DECAY = _float("FINETUNE_WEIGHT_DECAY", 0.01)
LR_SCHEDULER = _env("FINETUNE_LR_SCHEDULER", "cosine")
SEED = _int("FINETUNE_SEED", 42)

# Berapa epoch per dataset saat training bertahap.
# grounded diturunkan 2.0 -> 1.0: datanya paling repetitif (template) sehingga
# paling rawan overfit; pantau val loss per-epoch (lihat train_qlora.py).
EPOCHS = {
    "intent": _float("FINETUNE_EPOCHS_INTENT", 2.0),
    "faq": _float("FINETUNE_EPOCHS_FAQ", 3.0),
    "grounded": _float("FINETUNE_EPOCHS_GROUNDED", 1.0),
}

# Urutan training bertahap (data terbersih dulu).
STAGE_ORDER = ["intent", "faq", "grounded"]


def summary():
    return {
        "base_model": BASE_MODEL,
        "lora_r": LORA_R,
        "lora_alpha": LORA_ALPHA,
        "lora_dropout": LORA_DROPOUT,
        "load_in_4bit": LOAD_IN_4BIT,
        "max_seq_len": MAX_SEQ_LEN,
        "learning_rate": LEARNING_RATE,
        "batch_size": BATCH_SIZE,
        "grad_accum": GRAD_ACCUM,
        "warmup_ratio": WARMUP_RATIO,
        "weight_decay": WEIGHT_DECAY,
        "lr_scheduler": LR_SCHEDULER,
        "seed": SEED,
        "epochs": EPOCHS,
        "stage_order": STAGE_ORDER,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(summary(), indent=2, ensure_ascii=False))
