# -*- coding: utf-8 -*-
"""finetune/build_all.py — orkestrator pembuatan 3 dataset LoRA.

Jalankan SEMUA builder lalu tulis manifest ringkas:
    python -m finetune.build_all
    python -m finetune.build_all --only intent
Output JSONL: _runs/finetune/ (di-gitignore).
"""
import os
import sys
import json
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finetune import common as C  # noqa: E402
from finetune import build_intent, build_faq, build_grounded  # noqa: E402

_BUILDERS = {
    "intent": (build_intent.build, "intent.jsonl"),
    "faq": (build_faq.build, "faq.jsonl"),
    "grounded": (build_grounded.build, "grounded.jsonl"),
}


def run(only=None, val_ratio=0.05):
    manifest = {"base_model": C.DEFAULT_BASE_MODEL, "data_dir": C.data_dir(), "datasets": {}}
    targets = [only] if only else list(_BUILDERS.keys())
    for name in targets:
        fn, out = _BUILDERS[name]
        try:
            samples = fn()
        except Exception as e:
            print("[build_all] %s gagal:" % name, e)
            samples = []
        info = C.write_jsonl(out, samples, val_ratio=val_ratio)
        manifest["datasets"][name] = info
        print("[build_all] %-9s -> %s" % (name, info))
    mpath = os.path.join(C.data_dir(), "manifest.json")
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print("[build_all] manifest:", mpath)
    return manifest


def main():
    ap = argparse.ArgumentParser(
        description="Bangun dataset LoRA (SFT) dari knowledge base camerad.")
    ap.add_argument("--only", choices=list(_BUILDERS.keys()), help="Bangun satu dataset saja.")
    ap.add_argument("--val-ratio", type=float, default=0.05, help="Porsi validasi (default 0.05).")
    a = ap.parse_args()
    run(only=a.only, val_ratio=a.val_ratio)


if __name__ == "__main__":
    main()
