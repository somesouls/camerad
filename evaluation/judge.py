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

CROSS-MODEL JUDGING (opsional): agar juri tidak sama dengan model penjawab
(mengurangi bias 'menilai jawaban sendiri' dan under-count halusinasi), set di
.env salah satu dari:
    EVAL_JUDGE_MODEL=gpt-4o            (provider ikut LLM_PROVIDER)
    EVAL_JUDGE_PROVIDER=gemini + EVAL_JUDGE_MODEL=gemini-1.5-pro
    EVAL_JUDGE_DEPLOYMENT=<deployment>  (untuk Azure)
Bila tak diisi, juri memakai model penjawab (llm_client.chat) seperti semula.
"""
import os
import re
import json
import time

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
    "PENTING — ACUAN BERUPA AKSI-AGEN: acuan berasal dari agen live-chat manusia "
    "dan SERING memuat aksi yang HANYA bisa dilakukan agen, bukan chatbot "
    "pengetahuan, yaitu: (a) meminta data pribadi untuk verifikasi/lookup "
    "(NIK, NPWP, nama, alamat terdaftar, email, no. HP); (b) menjanjikan "
    "pengecekan status/data di sistem; (c) mengalihkan ke AR/KPP/loket atau "
    "menyatakan 'tidak dapat diproses via chat'. Chatbot TIDAK dapat memverifikasi "
    "identitas atau mengakses data pribadi. Maka:\n"
    "  * Jika inti acuan adalah verifikasi/pengumpulan data pribadi atau "
    "pengalihan ke AR/KPP, dan mesin dengan tepat mengarahkan ke kanal yang benar "
    "ATAU memberi panduan swalayan yang akurat, nilai abstain_benar (atau benar "
    "bila panduannya akurat). JANGAN menilai salah semata-mata karena mesin tidak "
    "meminta data pribadi atau tidak melakukan lookup.\n"
    "  * Tetap nilai salah/halusinasi bila mesin memberi fakta yang keliru atau "
    "mengarang pasal/tautan/langkah.\n\n"
    "Jika tidak ada JAWABAN ACUAN, nilai berdasarkan kewajaran & konsistensi "
    "internal: jawaban spesifik tak terdukung => halusinasi; abstain yang wajar "
    "=> abstain_benar.\n\n"
    "Balas HANYA JSON valid: "
    '{"verdict":"...","grounded":true/false,"skor":0..1,"alasan":"<=200 char"}. '
    "skor = keyakinan jawaban benar/tepat (0..1)."
)


def _cfg(name, default=None):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


# --- Client PENILAI opsional (cross-model). Diinisialisasi malas & sekali. ---
_jclient = None
_jprovider = None
_jmodel = None
_jinit_done = False


def _init_judge():
    """Siapkan client juri bila EVAL_JUDGE_* diset. Return True bila client juri
    terpisah aktif; False -> fallback ke llm_client.chat (model penjawab)."""
    global _jclient, _jprovider, _jmodel, _jinit_done
    if _jinit_done:
        return _jclient is not None
    _jinit_done = True
    prov_raw = _cfg("EVAL_JUDGE_PROVIDER")
    model_raw = _cfg("EVAL_JUDGE_MODEL") or _cfg("EVAL_JUDGE_DEPLOYMENT")
    if not prov_raw and not model_raw:
        return False   # tidak dikonfigurasi -> pakai model penjawab
    provider = (prov_raw or _cfg("LLM_PROVIDER", "openai") or "").strip().lower()
    try:
        if provider == "openai":
            from openai import OpenAI
            api_key = _cfg("OPENAI_API_KEY")
            if not api_key:
                return False
            _jmodel = model_raw or _cfg("OPENAI_MODEL", "gpt-4o-mini")
            kwargs = {"api_key": api_key}
            base_url = _cfg("OPENAI_BASE_URL")
            if base_url:
                kwargs["base_url"] = base_url
            _jclient = OpenAI(**kwargs)
        elif provider in ("azure", "azure_openai", "azureopenai"):
            from openai import AzureOpenAI
            api_key = _cfg("AZURE_OPENAI_API_KEY")
            endpoint = _cfg("AZURE_OPENAI_ENDPOINT")
            if not api_key or not endpoint:
                return False
            endpoint = endpoint.rstrip('/')
            if endpoint.lower().endswith('/openai/v1'):
                endpoint = endpoint[: -len('/openai/v1')]
            elif endpoint.lower().endswith('/openai'):
                endpoint = endpoint[: -len('/openai')]
            _jmodel = model_raw or _cfg("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")
            _jclient = AzureOpenAI(
                api_key=api_key, azure_endpoint=endpoint,
                api_version=_cfg("AZURE_OPENAI_API_VERSION", "2024-06-01"))
        elif provider in ("gemini", "google"):
            import google.generativeai as genai
            api_key = _cfg("GEMINI_API_KEY") or _cfg("GOOGLE_API_KEY")
            if not api_key:
                return False
            genai.configure(api_key=api_key)
            _jmodel = model_raw or _cfg("GEMINI_MODEL", "gemini-1.5-flash")
            _jclient = genai
        else:
            return False
        _jprovider = provider
        print("[eval_judge] juri cross-model: provider=%s model=%s" % (_jprovider, _jmodel),
              flush=True)
        return True
    except Exception as e:
        print("[eval_judge] gagal init juri cross-model, fallback ke penjawab: %s" % e,
              flush=True)
        _jclient = None
        return False


def _judge_llm(system, user, max_new_tokens, temperature=0.0):
    """Panggil model juri terpisah bila dikonfigurasi; jika tidak, pakai
    llm_client.chat (model penjawab)."""
    if not _init_judge():
        return llm_client.chat([{"role": "user", "content": user}], system=system,
                               max_new_tokens=max_new_tokens, temperature=temperature)
    last_err = None
    for attempt in range(1, 4):
        try:
            if _jprovider in ("openai", "azure", "azure_openai", "azureopenai"):
                msgs = [{"role": "system", "content": system},
                        {"role": "user", "content": user}]
                req = {"model": _jmodel, "messages": msgs, "temperature": temperature}
                if _jprovider in ("azure", "azure_openai", "azureopenai"):
                    req["max_completion_tokens"] = max(int(max_new_tokens), 1)
                else:
                    req["max_tokens"] = max(int(max_new_tokens), 1)
                resp = _jclient.chat.completions.create(**req)
                return (resp.choices[0].message.content or "").strip()
            model = _jclient.GenerativeModel(_jmodel, system_instruction=(system or None))
            resp = model.generate_content(user, generation_config={
                "max_output_tokens": max(int(max_new_tokens), 16),
                "temperature": temperature})
            return (getattr(resp, "text", "") or "").strip()
        except Exception as exc:
            last_err = exc
            print("[eval_judge] percobaan juri %d/3 gagal: %s" % (attempt, exc), flush=True)
            if attempt < 3:
                time.sleep(1.5 * attempt)
    raise RuntimeError("juri cross-model gagal: %s" % last_err)


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
        out = _judge_llm(_SYS, user, max_new_tokens=max_tokens, temperature=0.0)
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
