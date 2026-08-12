# -*- coding: utf-8 -*-
"""eval_judge.py — Penilai LLM-as-judge untuk jawaban mesin RAG.

Verdict per jawaban:
  benar         : akurat & konsisten dgn gold, berbasis konteks (grounded)
  salah         : menjawab tapi keliru / bertentangan dgn gold
  halusinasi    : klaim spesifik yang tidak terdukung / dikarang
  abstain_benar : mesin memilih tidak menjawab, dan itu memang tepat
  abstain_salah : mesin abstain padahal seharusnya bisa menjawab (gold ada)

Keluaran JSON: {verdict, grounded, skor, alasan}. Validasi manusia dilakukan
terpisah lewat dashboard (menimpa verdict penilai bila berbeda).
"""
import re
import json

import llm_client
import eval_db

_SYS = (
    "Anda auditor QA untuk chatbot pajak (DJP) berbasis RAG. Nilai SATU jawaban "
    "mesin terhadap pertanyaan pengguna, dibandingkan JAWABAN ACUAN (gold) bila "
    "tersedia. Nilai ketat: jawaban harus akurat, sesuai peraturan/prosedur, dan "
    "tidak mengarang.\n\n"
    "Pilih SATU verdict:\n"
    "- benar: menjawab dan akurat/konsisten dengan acuan.\n"
    "- salah: menjawab tetapi keliru atau bertentangan dengan acuan.\n"
    "- halusinasi: memuat klaim spesifik (angka, pasal, prosedur, tautan) yang "
    "tidak terdukung konteks atau tampak dikarang.\n"
    "- abstain_benar: mesin TIDAK menjawab (mengarahkan ke agen/Kring Pajak/kantor) "
    "dan itu memang tepat (di luar cakupan, atau acuan pun tak memberi jawaban pasti).\n"
    "- abstain_salah: mesin abstain padahal acuan menunjukkan pertanyaan bisa "
    "dijawab dengan jelas.\n\n"
    "Jika tidak ada JAWABAN ACUAN, nilai berdasarkan kewajaran & konsistensi "
    "internal: jawaban spesifik tak terdukung => halusinasi; abstain yang wajar "
    "=> abstain_benar.\n\n"
    "Balas HANYA JSON valid: "
    '{"verdict":"...","grounded":true/false,"skor":0..1,"alasan":"<=200 char"}. '
    "skor = keyakinan jawaban benar/tepat (0..1)."
)


def _parse(out):
    m = re.search(r"\{.*\}", out or "", re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _norm_verdict(v):
    v = (v or "").strip().lower()
    if v in eval_db.VERDICTS:
        return v
    if "halus" in v:
        return "halusinasi"
    if v.startswith("abstain") and "salah" in v:
        return "abstain_salah"
    if v.startswith("abstain"):
        return "abstain_benar"
    if "benar" in v:
        return "benar"
    if "salah" in v:
        return "salah"
    return ""


def judge_one(pertanyaan, gold, answer, abstain, sources_txt="", max_tokens=220):
    gold_blk = (gold or "").strip()
    parts = ["PERTANYAAN PENGGUNA:\n" + (pertanyaan or "").strip()]
    if gold_blk:
        parts.append("JAWABAN ACUAN (gold, dari agen live-chat):\n" + gold_blk[:1500])
    else:
        parts.append("JAWABAN ACUAN: (tidak tersedia — sumber chatbot; nilai coverage/kewajaran)")
    parts.append("STATUS MESIN: " + ("ABSTAIN (tidak menjawab / mengarahkan ke agen)" if abstain else "MENJAWAB"))
    if sources_txt:
        parts.append("SUMBER YANG DIRUJUK MESIN:\n" + sources_txt[:800])
    parts.append("JAWABAN MESIN:\n" + (answer or "").strip()[:1800])
    user = "\n\n".join(parts)
    try:
        out = llm_client.chat([{"role": "user", "content": user}], system=_SYS,
                              max_new_tokens=max_tokens, temperature=0.0)
    except Exception as e:
        return {"verdict": "", "grounded": False, "skor": None,
                "alasan": "penilai gagal: " + str(e)[:150], "ok": False}
    d = _parse(out)
    if not d:
        return {"verdict": "", "grounded": False, "skor": None,
                "alasan": "format penilai tak terbaca", "ok": False}
    v = _norm_verdict(d.get("verdict"))
    try:
        skor = float(d.get("skor")) if d.get("skor") is not None else None
    except Exception:
        skor = None
    return {"verdict": v, "grounded": bool(d.get("grounded")), "skor": skor,
            "alasan": str(d.get("alasan") or "")[:220], "ok": bool(v)}
