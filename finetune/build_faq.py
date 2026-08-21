# -*- coding: utf-8 -*-
"""finetune/build_faq.py — Dataset #2: FAQ multi-turn (sosmed X + livechat AWE).

Menjawab kebingungan #2 (follow-up question): satu UTAS percakapan dijadikan
SATU sampel multi-turn (user/assistant bergiliran). Model belajar konteks
lanjutan + KAPAN harus balik bertanya (klarifikasi).

Sumber: db.qa_index_db.collect_sosmed() & collect_awe() (AWE sudah bot-filter).
PII di-mask via common.pii_mask sebelum ditulis. Teks dibersihkan via
common.clean_train (buang scaffolding ### / tag kontrol / ",,,").
  * sosmed : pasangan per-tweet + conv_id -> dikelompokkan jadi multi-turn.
  * awe    : tiap percakapan sudah tergabung (semua giliran customer -> 1 Q,
             semua giliran agen manusia -> 1 A) => single-turn.

Jalankan:  python -m finetune.build_faq
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finetune import common as C  # noqa: E402


def _grouped(items):
    convs, singles = {}, []
    for it in (items or []):
        cid = C.clean(it.get("conv_id"))
        if cid:
            convs.setdefault((it.get("sumber") or "", cid), []).append(it)
        else:
            singles.append(it)
    return convs, singles


def build(limit_sosmed=5000, limit_awe=3000, min_len=3):
    try:
        from db import qa_index_db as qa
    except Exception as e:
        print("[faq] qa_index_db tidak tersedia:", e)
        return []
    try:
        sos = qa.collect_sosmed(limit_sosmed)
    except Exception:
        sos = []
    try:
        awe = qa.collect_awe(limit_awe)
    except Exception:
        awe = []
    samples = []
    convs, singles = _grouped(sos)
    # multi-turn (utas sosmed) — urut kronologis ~ ref_id
    for (sumber, cid), turns in convs.items():
        turns = sorted(turns, key=lambda x: str(x.get("ref_id") or ""))
        msgs = [{"role": "system", "content": C.SYS_CHATBOT}]
        for t in turns:
            q = C.pii_mask(C.clean_train(t.get("question")))
            a = C.pii_mask(C.clean_train(t.get("answer")))
            if len(q) < min_len or len(a) < min_len:
                continue
            msgs.append({"role": "user", "content": q})
            msgs.append({"role": "assistant", "content": a})
        if len(msgs) >= 3:
            samples.append(C.sample(msgs, {"task": "faq", "source": sumber or "sosmed",
                                           "multiturn": True,
                                           "turns": (len(msgs) - 1) // 2,
                                           "conv_id": cid}))
    # single-turn (sosmed tanpa utas + AWE)
    for it in singles + list(awe or []):
        q = C.pii_mask(C.clean_train(it.get("question")))
        a = C.pii_mask(C.clean_train(it.get("answer")))
        if len(q) < min_len or len(a) < min_len:
            continue
        samples.append(C.sample(
            [{"role": "system", "content": C.SYS_CHATBOT},
             {"role": "user", "content": q},
             {"role": "assistant", "content": a}],
            {"task": "faq", "source": it.get("sumber") or "", "multiturn": False}))
    return samples


def main():
    s = build()
    info = C.write_jsonl("faq.jsonl", s, val_ratio=0.05)
    print("[faq] sampel:", len(s), "->", info)
    return info


if __name__ == "__main__":
    main()
