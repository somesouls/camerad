# -*- coding: utf-8 -*-
"""Bandingkan dua run evaluasi chatbot_intent (A/B) dari eval.db.

Read-only. Default: run chatbot_intent TERBARU vs baseline run-533
(chatbot_intent_02a911b100). Override:
    python banding_eval.py <run_baru> [run_lama]

Fokus pada RASIO (bukan jumlah) agar adil walau n berbeda.
"""
import os
import sys
import sqlite3
from collections import Counter

BASELINE_LAMA = "chatbot_intent_02a911b100"


def _db():
    f = os.environ.get("PIPELINE_EVAL_DB_FILE", "eval.db")
    c = sqlite3.connect(f)
    c.row_factory = sqlite3.Row
    return c, f


def _p95(vals):
    xs = sorted(v for v in vals if v is not None)
    if not xs:
        return None
    i = min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))
    return xs[i]


def metrik(c, rid):
    rows = c.execute(
        "select judge_verdict v, fallback_hit fb, latency_ms lat, domain dom "
        "from evalc_result where run_id=?", (rid,)).fetchall()
    n = len(rows)
    vc = Counter()
    domv = Counter()
    lat = []
    fb = 0
    for r in rows:
        v = r["v"] or "(kosong)"
        vc[v] += 1
        if r["fb"]:
            fb += 1
        if r["lat"] is not None:
            lat.append(r["lat"])
        if r["v"] in ("salah", "halusinasi"):
            domv[(r["dom"] or "?", r["v"])] += 1
    benar = vc.get("benar", 0)
    salah = vc.get("salah", 0)
    halu = vc.get("halusinasi", 0)
    denom = benar + salah + halu
    return {
        "n": n, "vc": vc, "domv": domv,
        "benar": benar, "salah": salah, "halu": halu,
        "presisi": (benar / denom) if denom else None,
        "coverage": (1 - fb / n) if n else None,
        "halu_rate": (halu / n) if n else None,
        "lat_avg": (sum(lat) / len(lat)) if lat else None,
        "lat_p95": _p95(lat),
    }


def _pct(x):
    return "-" if x is None else ("%.1f%%" % (100.0 * x))


def _num(x):
    return "-" if x is None else ("%.1f" % x)


def main():
    c, f = _db()
    run_baru = sys.argv[1] if len(sys.argv) > 1 else None
    run_lama = sys.argv[2] if len(sys.argv) > 2 else BASELINE_LAMA
    if not run_baru:
        row = c.execute(
            "select id from evalc_run where metode=? and id!=? and n_total>=50 "
            "order by started_at desc limit 1", ("chatbot_intent", run_lama)).fetchone()
        run_baru = row["id"] if row else None
    if not run_baru:
        print("Tidak menemukan run chatbot_intent baru (n>=50). Jalankan eval dulu.")
        return
    print("DB:", f)
    for tag, rid in (("BARU", run_baru), ("LAMA", run_lama)):
        r = c.execute("select id,n_total,started_at from evalc_run where id=?", (rid,)).fetchone()
        if r:
            print("  %s: %s | n_total=%s | %s" % (tag, r["id"], r["n_total"], r["started_at"]))
        else:
            print("  %s: %s | (metadata tidak ada)" % (tag, rid))
    mb = metrik(c, run_baru)
    ml = metrik(c, run_lama)

    def baris(nama, kb, kl, fmt=_pct):
        print("  %-18s BARU=%-10s LAMA=%-10s" % (nama, fmt(kb), fmt(kl)))

    print("")
    print("=" * 64)
    print("RINGKASAN RASIO (BARU vs LAMA)")
    print("=" * 64)
    print("  %-18s BARU=%-10s LAMA=%-10s" % ("n (dinilai)", mb["n"], ml["n"]))
    baris("coverage", mb["coverage"], ml["coverage"])
    baris("presisi_jawab", mb["presisi"], ml["presisi"])
    baris("halusinasi_rate", mb["halu_rate"], ml["halu_rate"])
    print("  %-18s BARU=%d/%d/%d   LAMA=%d/%d/%d" % (
        "benar/salah/halu", mb["benar"], mb["salah"], mb["halu"],
        ml["benar"], ml["salah"], ml["halu"]))
    baris("latency_avg(ms)", mb["lat_avg"], ml["lat_avg"], _num)
    baris("latency_p95(ms)", mb["lat_p95"], ml["lat_p95"], _num)
    print("")
    print("== distribusi verdict (BARU)")
    for k, v in mb["vc"].most_common():
        print("   %-16s %d (%s)" % (k, v, _pct(v / mb["n"] if mb["n"] else None)))
    print("== distribusi verdict (LAMA)")
    for k, v in ml["vc"].most_common():
        print("   %-16s %d (%s)" % (k, v, _pct(v / ml["n"] if ml["n"] else None)))
    print("")
    print("== salah+halusinasi per domain (BARU)")
    for (d, v), nn in sorted(mb["domv"].items(), key=lambda kv: -kv[1]):
        print("   %-10s %-11s %d" % (d, v, nn))
    print("== salah+halusinasi per domain (LAMA)")
    for (d, v), nn in sorted(ml["domv"].items(), key=lambda kv: -kv[1]):
        print("   %-10s %-11s %d" % (d, v, nn))


if __name__ == "__main__":
    main()
