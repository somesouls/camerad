# -*- coding: utf-8 -*-
"""
Training QLoRA LoRA adapter dengan Unsloth.

Pakai:
    python -m finetune.train_qlora --dataset intent
    python -m finetune.train_qlora --dataset faq --resume-from _runs/finetune/adapters/intent
    python -m finetune.train_qlora --staged   # intent -> faq -> grounded (kumulatif)

Catatan:
- Loss HANYA dihitung pada token ASISTEN (train_on_responses_only).
- Bila ada <dataset>.val.jsonl (dibuat write_jsonl val_ratio>0), dipakai sebagai
  eval_dataset dan val loss dievaluasi tiap epoch -> gampang lihat overfit
  (train loss turun tapi val loss naik).
- Semua dependency berat (torch/unsloth/trl/datasets) di-import di dalam fungsi
  supaya file ini tetap lolos py_compile di CI tanpa GPU.
- Output adapter default: _runs/finetune/adapters/<dataset>/
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from finetune import common as C  # noqa: E402
from finetune import train_config as TC  # noqa: E402


def _adapters_dir():
    d = os.path.join(C.data_dir(), "adapters")
    os.makedirs(d, exist_ok=True)
    return d


def _load_chat_dataset(path):
    from datasets import load_dataset
    return load_dataset("json", data_files=path, split="train")


def train_one(dataset, resume_from=None, epochs=None, out_dir=None):
    """Latih satu tahap. Return path adapter hasil."""
    from unsloth import FastLanguageModel
    from unsloth.chat_templates import train_on_responses_only
    from trl import SFTTrainer, SFTConfig
    import torch

    train_path = os.path.join(C.data_dir(), f"{dataset}.jsonl")
    if not os.path.exists(train_path):
        raise FileNotFoundError(
            f"Dataset belum dibangun: {train_path}. "
            "Jalankan `python -m finetune.build_all` dulu."
        )

    out_dir = out_dir or os.path.join(_adapters_dir(), dataset)
    epochs = epochs if epochs is not None else TC.EPOCHS.get(dataset, 2.0)

    # Jika resume_from berisi path adapter, from_pretrained memuat LoRA-nya.
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=resume_from or TC.BASE_MODEL,
        max_seq_length=TC.MAX_SEQ_LEN,
        load_in_4bit=TC.LOAD_IN_4BIT,
        dtype=None,
    )
    if not resume_from:
        model = FastLanguageModel.get_peft_model(
            model,
            r=TC.LORA_R,
            lora_alpha=TC.LORA_ALPHA,
            lora_dropout=TC.LORA_DROPOUT,
            target_modules=TC.TARGET_MODULES,
            bias="none",
            use_gradient_checkpointing="unsloth",
            random_state=TC.SEED,
        )

    def _fmt(batch):
        texts = [
            tokenizer.apply_chat_template(
                msgs, tokenize=False, add_generation_prompt=False
            )
            for msgs in batch["messages"]
        ]
        return {"text": texts}

    ds = _load_chat_dataset(train_path).map(_fmt, batched=True)

    # Eval split opsional (dibuat write_jsonl val_ratio>0).
    val_path = os.path.join(C.data_dir(), f"{dataset}.val.jsonl")
    eval_ds = None
    if os.path.exists(val_path):
        eval_ds = _load_chat_dataset(val_path).map(_fmt, batched=True)
        print(f"[train_qlora] eval split: {val_path} ({len(eval_ds)} sampel)", flush=True)

    sft_kwargs = dict(
        per_device_train_batch_size=TC.BATCH_SIZE,
        gradient_accumulation_steps=TC.GRAD_ACCUM,
        warmup_ratio=TC.WARMUP_RATIO,
        num_train_epochs=epochs,
        learning_rate=TC.LEARNING_RATE,
        lr_scheduler_type=TC.LR_SCHEDULER,
        weight_decay=TC.WEIGHT_DECAY,
        seed=TC.SEED,
        logging_steps=10,
        optim="adamw_8bit",
        bf16=torch.cuda.is_bf16_supported(),
        fp16=not torch.cuda.is_bf16_supported(),
        output_dir=os.path.join(out_dir, "_trainer"),
        report_to="none",
    )
    if eval_ds is not None:
        sft_kwargs["eval_strategy"] = "epoch"
        sft_kwargs["per_device_eval_batch_size"] = TC.BATCH_SIZE

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=ds,
        eval_dataset=eval_ds,
        dataset_text_field="text",
        max_seq_length=TC.MAX_SEQ_LEN,
        args=SFTConfig(**sft_kwargs),
    )

    # Loss hanya pada token asisten (Qwen2.5 chat template).
    trainer = train_on_responses_only(
        trainer,
        instruction_part="<|im_start|>user\n",
        response_part="<|im_start|>assistant\n",
    )

    trainer.train()

    model.save_pretrained(out_dir)
    tokenizer.save_pretrained(out_dir)
    print(f"[train_qlora] adapter tersimpan: {out_dir}", flush=True)
    return out_dir


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", choices=TC.STAGE_ORDER)
    ap.add_argument("--resume-from", default=None)
    ap.add_argument("--epochs", type=float, default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--staged", action="store_true",
                    help="Latih intent->faq->grounded berurutan (kumulatif).")
    args = ap.parse_args()

    if args.staged:
        prev = None
        for stage in TC.STAGE_ORDER:
            print(f"\n=== TAHAP: {stage} (resume_from={prev}) ===", flush=True)
            prev = train_one(stage, resume_from=prev)
        print(f"\n[train_qlora] selesai. Adapter final: {prev}", flush=True)
        return

    if not args.dataset:
        ap.error("wajib --dataset atau --staged")
    train_one(args.dataset, resume_from=args.resume_from,
              epochs=args.epochs, out_dir=args.out)


if __name__ == "__main__":
    main()
