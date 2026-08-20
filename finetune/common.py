# -*- coding: utf-8 -*-
"""finetune/common.py — util bersama pembuatan dataset LoRA (SFT).

Semua builder menghasilkan sampel format CHAT terpadu (OpenAI/ShareGPT):
    {"messages": [{"role", "content"}, ...], "meta": {...}}
Format ini langsung dipakai Unsloth/Axolotl (chat template) untuk QLoRA, dan
sama persis dengan skema pesan saat serving vLLM (OpenAI-compatible) sehingga
perbandingan RAG vs LoRA adil.

Hanya STDLIB + reuse common.pii_mask (opsional, gagal-anggun). Output ditulis ke
_runs/finetune/ (sudah masuk .gitignore) — artefak generatif, tidak ikut commit.
"""
import os
import re
import json
import hashlib
import random

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # finetune/ -> root repo


def data_dir():
    d = os.environ.get("FINETUNE_DATA_DIR") or os.path.join(_BASE_DIR, "_runs", "finetune")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


# Rekomendasi default (override via env FINETUNE_BASE_MODEL). Lihat README.
DEFAULT_BASE_MODEL = os.environ.get("FINETUNE_BASE_MODEL", "Qwen/Qwen2.5-7B-Instruct")

SYS_INTENT = (
    "Klasifikasikan maksud (intent) pesan pengguna ke SATU nama intent baku "
    "sesuai kebijakan analis Kring Pajak. Jawab HANYA dengan nama intent, tanpa "
    "penjelasan tambahan."
)

SYS_CHATBOT = (
    "Kamu asisten pajak Kring Pajak berbahasa Indonesia. Jawab ringkas, sopan, "
    "dan akurat. Bila informasi dari pengguna belum cukup untuk menjawab pasti, "
    "ajukan pertanyaan klarifikasi lebih dulu sebelum menjawab."
)

SYS_GROUNDED = (
    "Kamu menjawab HANYA berdasarkan Konteks yang diberikan. Dilarang mengarang "
    "di luar konteks. Selalu cantumkan dasar hukum/sumber yang relevan. Bila "
    "konteks tidak memuat jawabannya, katakan tidak ada dasarnya di konteks."
)


def clean(t):
    return re.sub(r"\s+", " ", (t or "").strip())


def pii_mask(t):
    """Mask PII via common.pii_mask bila tersedia; kalau tidak, kembalikan apa adanya."""
    try:
        import common.pii_mask as pm
        return pm.mask_text(t)
    except Exception:
        return t


def sample(messages, meta=None):
    return {"messages": messages, "meta": meta or {}}


def _sig(rec):
    msgs = rec.get("messages") or []
    body = "\n".join("%s|%s" % (m.get("role"), clean(m.get("content"))) for m in msgs)
    return hashlib.sha1(body.encode("utf-8")).hexdigest()


def _dump(path, records):
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def write_jsonl(name, records, dedup=True, shuffle=True, seed=42, val_ratio=0.0):
    """Tulis records ke _runs/finetune/<name> (+ .val.jsonl bila val_ratio>0).
    Return dict ringkas berisi path & jumlah."""
    d = data_dir()
    records = list(records or [])
    if dedup:
        seen, uniq = set(), []
        for r in records:
            s = _sig(r)
            if s in seen:
                continue
            seen.add(s)
            uniq.append(r)
        records = uniq
    if shuffle:
        random.Random(seed).shuffle(records)
    n_val = int(len(records) * val_ratio) if val_ratio else 0
    val, train = records[:n_val], records[n_val:]
    train_path = os.path.join(d, name)
    _dump(train_path, train)
    out = {"train_path": train_path, "train": len(train), "total": len(records)}
    if n_val:
        val_path = os.path.join(d, name.replace(".jsonl", ".val.jsonl"))
        _dump(val_path, val)
        out["val_path"] = val_path
        out["val"] = len(val)
    return out
