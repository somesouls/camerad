# -*- coding: utf-8 -*-
"""Uji-cepat (sanity check) hasil LoRA — ngobrol langsung dgn adapter terlatih.

Jalankan di WSL2, di env conda 'train' (yang dipakai training):

    # mode interaktif, pakai adapter final 'grounded'
    python -m finetune.chat_test

    # uji adapter tahap tertentu
    python -m finetune.chat_test --adapter intent
    python -m finetune.chat_test --adapter faq

    # sekali tanya lalu keluar
    python -m finetune.chat_test -p "Apa itu PPh Pasal 21?"

    # bandingkan dgn base model (tanpa LoRA)
    python -m finetune.chat_test --adapter base -p "Apa itu PPh Pasal 21?"

    # mode grounded: beri konteks yg harus jadi dasar jawaban
    python -m finetune.chat_test --system grounded \
        --context "Pasal 17 UU PPh: tarif ..." -p "Berapa tarifnya?"

Catatan: dependency berat (torch/unsloth) diimport DI DALAM fungsi supaya file
ini tetap lolos py_compile di CI tanpa GPU.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from finetune import common as C  # noqa: E402
from finetune import train_config as TC  # noqa: E402

_SYS = {
    "chatbot": C.SYS_CHATBOT,
    "grounded": C.SYS_GROUNDED,
    "intent": C.SYS_INTENT,
}


def _resolve_model(adapter):
    """Return (model_name_or_path, is_adapter)."""
    if adapter in ("base", "none", ""):
        return TC.BASE_MODEL, False
    if os.path.isdir(adapter):
        return adapter, True
    path = os.path.join(C.data_dir(), "adapters", adapter)
    if not os.path.isdir(path):
        raise SystemExit(
            "[chat_test] adapter tak ditemukan: %s\n"
            "Pilih: intent | faq | grounded | base, atau beri path folder adapter."
            % path
        )
    return path, True


def _system_text(value):
    if not value:
        return _SYS["chatbot"]
    # kata kunci -> teks system baku; selain itu dianggap teks custom apa adanya
    return _SYS.get(value, value)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", default="grounded",
                    help="intent|faq|grounded|base atau path folder (default grounded)")
    ap.add_argument("-p", "--prompt", default=None, help="tanya sekali lalu keluar")
    ap.add_argument("--system", default="chatbot",
                    help="chatbot|grounded|intent atau teks system custom")
    ap.add_argument("--context", default=None,
                    help="konteks tambahan (untuk mode grounded)")
    ap.add_argument("--max-new-tokens", type=int, default=512)
    ap.add_argument("--temperature", type=float, default=0.7)
    args = ap.parse_args()

    from unsloth import FastLanguageModel

    model_name, is_adapter = _resolve_model(args.adapter)
    label = ("LoRA:" + args.adapter) if is_adapter else "BASE (tanpa LoRA)"
    print("[chat_test] memuat %s -> %s" % (label, model_name), flush=True)

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=model_name,
        max_seq_length=TC.MAX_SEQ_LEN,
        load_in_4bit=TC.LOAD_IN_4BIT,
        dtype=None,
    )
    FastLanguageModel.for_inference(model)

    system_text = _system_text(args.system)

    def ask(question):
        user = question
        if args.context:
            user = "Konteks:\n%s\n\nPertanyaan: %s" % (args.context, question)
        messages = [
            {"role": "system", "content": system_text},
            {"role": "user", "content": user},
        ]
        inputs = tokenizer.apply_chat_template(
            messages, tokenize=True, add_generation_prompt=True, return_tensors="pt"
        ).to(model.device)
        out = model.generate(
            input_ids=inputs,
            max_new_tokens=args.max_new_tokens,
            temperature=args.temperature,
            do_sample=args.temperature > 0,
            use_cache=True,
        )
        text = tokenizer.decode(out[0][inputs.shape[1]:], skip_special_tokens=True)
        return text.strip()

    if args.prompt:
        print("\n" + ask(args.prompt) + "\n", flush=True)
        return

    print("[chat_test] mode interaktif. Ketik pertanyaan; 'exit'/'quit' untuk keluar.\n",
          flush=True)
    while True:
        try:
            q = input("anda> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not q:
            continue
        if q.lower() in ("exit", "quit", "keluar"):
            break
        print("\nmodel> " + ask(q) + "\n", flush=True)


if __name__ == "__main__":
    main()
