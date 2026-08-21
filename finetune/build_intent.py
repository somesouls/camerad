# -*- coding: utf-8 -*-
"""finetune/build_intent.py — Dataset #1: klasifikasi intent (utterance -> intent).

Sumber (knowledge base yang sudah ada):
  * Peta Intent analis : knowledge.intentmap_db.list_intents()
      contoh_utterance + cakupan  => label intent (kebijakan analis; sengaja
      'melawan' semantik naif, mis. 'lupa email & no hp' -> Perubahan Data).
  * Katalog Dialogflow : knowledge.intentmap_db.catalog_list()
      training_phrase_contoh      => label intent (frasa training asli).

Kandidat LoRA TERBAIK untuk dibangun pertama: data bersih & berlabel, dan
mengajarkan keputusan intent yang tak bisa ditebak dari makna literal.
Utterance dibersihkan via common.clean_train; label intent tetap apa adanya.

Jalankan:  python -m finetune.build_intent
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finetune import common as C  # noqa: E402


def build(limit=8000, min_len=3):
    samples = []
    try:
        from knowledge import intentmap_db as im
    except Exception as e:
        print("[intent] intentmap_db tidak tersedia:", e)
        return samples
    try:
        c = im.init_db(im.connect())
    except Exception as e:
        print("[intent] gagal konek DB:", e)
        return samples
    try:
        try:
            im.init_catalog(c)
        except Exception:
            pass
        # (a) Peta Intent analis: contoh_utterance + cakupan -> intent
        try:
            rows = im.list_intents(c, limit=limit)
        except Exception:
            rows = []
        for r in rows:
            if (r.get("status") or "aktif") != "aktif":
                continue
            intent = C.clean(r.get("intent"))
            if not intent:
                continue
            seen = set()
            for u in list(r.get("contoh_utterance") or []) + list(r.get("cakupan") or []):
                u = C.clean_train(u)
                if len(u) < min_len or u.lower() in seen:
                    continue
                seen.add(u.lower())
                samples.append(C.sample(
                    [{"role": "system", "content": C.SYS_INTENT},
                     {"role": "user", "content": u},
                     {"role": "assistant", "content": intent}],
                    {"task": "intent", "source": "peta_intent",
                     "struktur": r.get("struktur") or "mandiri",
                     "parent": r.get("parent") or ""}))
        # (b) Katalog training phrase Dialogflow -> intent
        try:
            crows = im.catalog_list(c, limit=limit)
        except Exception:
            crows = []
        for r in crows:
            intent = C.clean(r.get("intent"))
            if not intent:
                continue
            for u in (r.get("training_phrase_contoh") or []):
                u = C.clean_train(u)
                if len(u) < min_len:
                    continue
                samples.append(C.sample(
                    [{"role": "system", "content": C.SYS_INTENT},
                     {"role": "user", "content": u},
                     {"role": "assistant", "content": intent}],
                    {"task": "intent", "source": "training_phrase"}))
    finally:
        try:
            c.close()
        except Exception:
            pass
    return samples


def main():
    s = build()
    info = C.write_jsonl("intent.jsonl", s, val_ratio=0.05)
    print("[intent] sampel:", len(s), "->", info)
    return info


if __name__ == "__main__":
    main()
