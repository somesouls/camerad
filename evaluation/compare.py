# -*- coding: utf-8 -*-
"""evaluation/compare.py — Bandingkan RAG vs LoRA (fine-tuning) berdampingan.

Tiga mode per pertanyaan:
  - rag_base : LLM base + retrieval RAG (baseline saat ini)
  - lora     : LLM fine-tuned (adapter) TANPA retrieval (murni parametrik)
  - lora_rag : LLM fine-tuned (adapter) + retrieval RAG

Konteks RAG diambil dari pipeline retrieval ASLI (rag.engine: effective_sources
+ _assemble) TANPA generate, sehingga tiga mode dinilai dengan konteks yang sama
-> perbandingan adil. Generasi memakai server lokal OpenAI-compatible
(finetune.serve_local) yang menyajikan base + semua adapter; mode dipilih lewat
nama model.

Prasyarat: jalankan `python -m finetune.serve_local` lebih dulu (default :8001).

CLI:
    python -m evaluation.compare -q "UMKM kena pajak apa?"
    python -m evaluation.compare -q "cara lapor SPT tahunan" --adapter camerad-grounded --judge
    python -m evaluation.compare --file soal.txt --profil chatbot

Catatan: dependency berat (model retrieval, dll) diimport lewat rag.engine saat
runtime; file ini sendiri hanya butuh stdlib + modul aplikasi.
"""
import argparse
import json
import os
import sys
import time
import datetime as _dt
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import rag.engine as rag_engine  # noqa: E402
import rag.config_db as rcfg  # noqa: E402
import finetune.common as fc  # noqa: E402

try:
    import evaluation.judge as eval_judge  # noqa: E402
except Exception:            # pragma: no cover
    eval_judge = None


def _base_url():
    return (os.environ.get("VLLM_BASE_URL")
            or os.environ.get("LOCAL_LLM_BASE_URL")
            or "http://127.0.0.1:8001/v1")


def ask_local(model, messages, temperature=0.3, max_tokens=512):
    """Panggil server lokal OpenAI-compatible. Return (teks, latensi_ms)."""
    url = _base_url().rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": float(temperature),
        "max_tokens": int(max_tokens),
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + (os.environ.get("VLLM_API_KEY") or "sk-local")},
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=600) as resp:
        out = json.loads(resp.read().decode("utf-8"))
    dt = (time.time() - t0) * 1000.0
    text = (out.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
    return text, dt


def retrieve_context(question, profil="chatbot", override=None):
    """Ambil konteks + sumber dari pipeline RAG asli TANPA generate."""
    profile = rcfg.get_profile(profil) or rcfg.get_profile("chatbot") or {}
    q = (question or "").strip()
    try:
        allowed = rag_engine.effective_sources(profile, override)
        context, sources = rag_engine._assemble(allowed, {}, q)
    except Exception as e:
        context, sources = "", []
        print("[compare] retrieval gagal: %s" % str(e)[:200], flush=True)
    return context, sources, profile


def _fallback_text(profile):
    return (profile.get("fallback") or rcfg.FALLBACK_DEFAULT or "").strip()


def _metrics(answer, context, sources, fallback):
    ans = (answer or "").strip()
    fb = (fallback or "").strip()
    fallback_hit = bool(ans) and bool(fb) and ans[:40].lower() == fb[:40].lower()
    low = ans.lower()
    has_ref = ("dasar" in low or "pasal" in low or "peraturan" in low
               or any(str(s.get("judul", "")).lower() in low for s in (sources or []) if s.get("judul")))
    return {
        "panjang": len(ans),
        "fallback_hit": fallback_hit,
        "ada_rujukan": bool(has_ref),
    }


def compare_one(question, adapter="camerad-grounded", base_model=None,
                profil="chatbot", judge=False, temperature=0.3, max_tokens=512):
    base_model = base_model or os.environ.get("FINETUNE_BASE_MODEL", "Qwen/Qwen2.5-7B-Instruct")
    q = (question or "").strip()
    context, sources, profile = retrieve_context(q, profil)
    fallback = _fallback_text(profile)
    sumber_txt = rag_engine._format_sumber(sources)
    system_rag = rag_engine._render_prompt(
        profile.get("system_prompt"), context, sumber_txt, fallback)

    specs = [
        ("rag_base", base_model, system_rag, context, sources),
        ("lora", adapter, fc.SYS_CHATBOT, "", []),
        ("lora_rag", adapter, system_rag, context, sources),
    ]
    modes = {}
    for name, model, system, ctx, srcs in specs:
        try:
            text, ms = ask_local(
                model,
                [{"role": "system", "content": system},
                 {"role": "user", "content": q}],
                temperature=temperature, max_tokens=max_tokens)
            rec = {"model": model, "answer": text, "latency_ms": round(ms, 1)}
            rec.update(_metrics(text, ctx, srcs, fallback))
        except Exception as e:
            rec = {"model": model, "answer": "", "error": str(e)[:200],
                   "latency_ms": None, "panjang": 0, "fallback_hit": False,
                   "ada_rujukan": False}
        if judge and eval_judge is not None and rec.get("answer"):
            try:
                jr = eval_judge.judge_one(q, "", rec["answer"],
                                          bool(rec.get("fallback_hit")), sumber_txt)
                rec["judge_verdict"] = jr.get("verdict")
                rec["judge_skor"] = jr.get("skor")
                rec["judge_alasan"] = jr.get("alasan")
            except Exception as e:
                rec["judge_alasan"] = "juri gagal: " + str(e)[:120]
        modes[name] = rec

    return {
        "question": q,
        "profil": profile.get("id") or profil,
        "adapter": adapter,
        "base_model": base_model,
        "context_chars": len(context or ""),
        "sources": [{"sumber": s.get("sumber", ""), "judul": s.get("judul", "")}
                    for s in (sources or [])],
        "modes": modes,
    }


def compare_many(questions, **kw):
    return [compare_one(q, **kw) for q in questions if (q or "").strip()]


def _print_report(res):
    print("\n" + "=" * 78, flush=True)
    print("Q: %s" % res["question"], flush=True)
    print("   (profil=%s, adapter=%s, konteks=%d char, %d sumber)" % (
        res["profil"], res["adapter"], res["context_chars"], len(res["sources"])), flush=True)
    labels = {"rag_base": "RAG (base LLM)",
              "lora": "LoRA saja (tanpa RAG)",
              "lora_rag": "LoRA + RAG"}
    for name in ("rag_base", "lora", "lora_rag"):
        r = res["modes"].get(name) or {}
        print("\n--- %s [%s] ---" % (labels[name], r.get("model", "")), flush=True)
        badge = []
        if r.get("latency_ms") is not None:
            badge.append("%.0f ms" % r["latency_ms"])
        badge.append("%d char" % r.get("panjang", 0))
        if r.get("fallback_hit"):
            badge.append("FALLBACK")
        if r.get("ada_rujukan"):
            badge.append("ada-rujukan")
        if r.get("judge_verdict"):
            badge.append("juri=%s(%s)" % (r.get("judge_verdict"), r.get("judge_skor")))
        if r.get("error"):
            badge.append("ERROR:" + r["error"])
        print("[" + " | ".join(badge) + "]", flush=True)
        print((r.get("answer") or "").strip() or "(kosong)", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-q", "--question", default=None, help="satu pertanyaan")
    ap.add_argument("--file", default=None, help="file berisi 1 pertanyaan per baris")
    ap.add_argument("--adapter", default="camerad-grounded",
                    help="nama model LoRA di server (default camerad-grounded)")
    ap.add_argument("--base-model", default=None, help="nama model base di server")
    ap.add_argument("--profil", default="chatbot", help="profil RAG (chatbot|agent)")
    ap.add_argument("--judge", action="store_true", help="nilai dgn LLM-as-judge (butuh provider cloud)")
    ap.add_argument("--temperature", type=float, default=0.3)
    ap.add_argument("--max-tokens", type=int, default=512)
    ap.add_argument("--out", default=None, help="path JSON hasil (default _runs/finetune/compare_*.json)")
    args = ap.parse_args()

    questions = []
    if args.question:
        questions.append(args.question)
    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            questions += [ln.strip() for ln in f if ln.strip()]
    if not questions:
        raise SystemExit("Beri -q \"pertanyaan\" atau --file soal.txt")

    results = compare_many(
        questions, adapter=args.adapter, base_model=args.base_model,
        profil=args.profil, judge=args.judge,
        temperature=args.temperature, max_tokens=args.max_tokens)

    for res in results:
        _print_report(res)

    out = args.out
    if not out:
        stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        out = os.path.join(fc.data_dir(), "compare_%s.json" % stamp)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("\n[compare] hasil lengkap tersimpan: %s" % out, flush=True)


if __name__ == "__main__":
    main()
