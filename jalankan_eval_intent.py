# -*- coding: utf-8 -*-
"""jalankan_eval_intent.py — Luncurkan SATU run eval chatbot_intent lalu tunggu
sampai selesai (blocking) dan cetak metrik. Terminal-only, tanpa menu web.

Catatan: eval_chatbot.start_intent() berjalan di thread daemon, jadi TIDAK bisa
lewat 'python -c ...' (proses keburu keluar dan mematikan worker). Skrip ini
menahan proses tetap hidup sambil memantau progres.

Contoh:
    # replikasi penuh run-533 (top_n=100, per_intent=12, ~n 500-an, juri ON)
    python jalankan_eval_intent.py

    # sinyal cepat: batasi 150 soal bertrafik tertinggi
    python jalankan_eval_intent.py --limit 150

    # sinyal cepat via sampling lebih kecil
    python jalankan_eval_intent.py --top-n 60 --per-intent 6

Butuh koneksi LLM (Azure) untuk menjawab + juri. .env dimuat otomatis.
"""
import argparse
import json
import time

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import eval_chatbot as ec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profil", default="chatbot")
    ap.add_argument("--top-n", type=int, default=100)
    ap.add_argument("--per-intent", type=int, default=12)
    ap.add_argument("--window", default="90d")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--tanpa-juri", action="store_true")
    ap.add_argument("--poll", type=int, default=5)
    a = ap.parse_args()

    res = ec.start_intent(profil=a.profil, top_n=a.top_n, window=a.window,
                          per_intent=a.per_intent, judge=(not a.tanpa_juri),
                          limit=a.limit)
    rid = res.get("run_id")
    if not rid:
        print("GAGAL start:", json.dumps(res, ensure_ascii=False))
        return
    print("run_id =", rid, "| n_total =", res.get("n_total"),
          "| juri =", (not a.tanpa_juri))
    print("Menunggu sampai selesai... (jangan tutup terminal; Ctrl+C membatalkan pemantauan)")
    last = -1
    while True:
        time.sleep(max(1, a.poll))
        st = ec.status(rid)
        run = (st or {}).get("run") or {}
        status = run.get("status")
        done = run.get("n_done")
        if done != last:
            print("  status=%s | %s/%s" % (status, done, run.get("n_total")))
            last = done
        if status in ("done", "error"):
            print("")
            print("STATUS AKHIR:", status, "| run_id:", rid)
            if run.get("note"):
                print("catatan:", run.get("note"))
            print("METRIK:", json.dumps(run.get("metrik"), ensure_ascii=False, indent=2))
            print("")
            print("Langkah berikut: python banding_eval.py")
            break


if __name__ == "__main__":
    main()
