# -*- coding: utf-8 -*-
"""Server LLM lokal OpenAI-compatible dengan backend Unsloth (transformers).

Dipakai sebagai alternatif vLLM ketika vLLM bentrok versi CUDA (mis. vLLM
ter-build utk CUDA 13 padahal sistem CUDA 12.8). Jalur ini memakai path inference
yang SAMA dengan finetune.chat_test, jadi pasti jalan di env 'train'.

Satu proses memuat base model + SEMUA adapter LoRA sekaligus (PEFT multi-adapter).
Pilih model/adapter per-request lewat field JSON 'model':
    - "camerad-grounded" / "camerad-faq" / "camerad-intent"  -> pakai adapter itu
    - "grounded" / "faq" / "intent"                          -> alias (auto camerad-*)
    - "base" atau nama base model                            -> tanpa LoRA

Jalankan (di WSL2, env conda 'train'):
    python -m finetune.serve_local                 # :8001
    python -m finetune.serve_local --port 8001

Lalu di .env aplikasi:
    LLM_PROVIDER=local
    VLLM_BASE_URL=http://127.0.0.1:8001/v1
    VLLM_MODEL=camerad-grounded

Endpoint:
    GET  /v1/models
    POST /v1/chat/completions   (non-streaming, OpenAI-compatible)

Butuh: fastapi + uvicorn (biasanya sudah ikut terpasang bareng vllm).
Kalau belum: pip install fastapi uvicorn

Catatan: dependency berat diimport DI DALAM fungsi supaya file ini tetap lolos
py_compile di CI tanpa GPU.
"""
import argparse
import os
import sys
import threading
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from finetune import common as C  # noqa: E402
from finetune import train_config as TC  # noqa: E402

_GEN_LOCK = threading.Lock()


def _discover_adapters():
    """Kembalikan list (nama_module, path) adapter di _runs/finetune/adapters."""
    base = os.path.join(C.data_dir(), "adapters")
    found = []
    if os.path.isdir(base):
        for name in sorted(os.listdir(base)):
            p = os.path.join(base, name)
            if os.path.isdir(p) and os.path.exists(
                os.path.join(p, "adapter_config.json")
            ):
                found.append(("camerad-" + name, p))
    return found


def _load_model():
    """Muat base + semua adapter. Return (model, tokenizer, adapter_names, has_adapters)."""
    from unsloth import FastLanguageModel

    print("[serve_local] memuat base: %s" % TC.BASE_MODEL, flush=True)
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=TC.BASE_MODEL,
        max_seq_length=TC.MAX_SEQ_LEN,
        load_in_4bit=TC.LOAD_IN_4BIT,
        dtype=None,
    )

    adapters = _discover_adapters()
    adapter_names = []
    has_adapters = False
    if adapters:
        from peft import PeftModel
        first_name, first_path = adapters[0]
        print("[serve_local] muat adapter: %s <- %s" % (first_name, first_path), flush=True)
        model = PeftModel.from_pretrained(model, first_path, adapter_name=first_name)
        adapter_names.append(first_name)
        for name, path in adapters[1:]:
            print("[serve_local] muat adapter: %s <- %s" % (name, path), flush=True)
            model.load_adapter(path, adapter_name=name)
            adapter_names.append(name)
        has_adapters = True
    else:
        print("[serve_local] tidak ada adapter; hanya melayani base.", flush=True)

    FastLanguageModel.for_inference(model)
    return model, tokenizer, adapter_names, has_adapters


def _resolve_model_name(model_req, adapter_names, has_adapters):
    """Petakan field 'model' request ke nama adapter, atau None utk base."""
    if not model_req:
        # default: adapter terakhir (grounded) bila ada, selain itu base
        return adapter_names[-1] if has_adapters else None
    m = str(model_req).strip()
    if m in ("base", TC.BASE_MODEL) or m.lower() == "base":
        return None
    if m in adapter_names:
        return m
    cand = "camerad-" + m
    if cand in adapter_names:
        return cand
    # tak dikenal -> fallback base (jangan bikin error keras)
    return None


def build_app(model, tokenizer, adapter_names, has_adapters):
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    import torch

    app = FastAPI(title="camerad-serve-local")
    served = [TC.BASE_MODEL] + list(adapter_names)

    def _generate(messages, model_req, max_new_tokens, temperature):
        sel = _resolve_model_name(model_req, adapter_names, has_adapters)
        enc = tokenizer.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=True,
            return_tensors="pt",
            return_dict=True,
        )
        input_ids = enc["input_ids"].to(model.device)
        attention_mask = enc["attention_mask"].to(model.device)
        gen_kwargs = dict(
            input_ids=input_ids,
            attention_mask=attention_mask,
            max_new_tokens=int(max_new_tokens),
            do_sample=temperature > 0,
            temperature=(temperature if temperature > 0 else None),
            use_cache=True,
            pad_token_id=(tokenizer.pad_token_id or tokenizer.eos_token_id),
        )
        with _GEN_LOCK:
            with torch.no_grad():
                if has_adapters and sel is None:
                    with model.disable_adapter():
                        out = model.generate(**gen_kwargs)
                else:
                    if has_adapters and sel is not None:
                        model.set_adapter(sel)
                    out = model.generate(**gen_kwargs)
        prompt_len = input_ids.shape[1]
        gen_ids = out[0][prompt_len:]
        text = tokenizer.decode(gen_ids, skip_special_tokens=True).strip()
        used = sel if sel is not None else "base"
        return text, prompt_len, int(gen_ids.shape[0]), used

    @app.get("/v1/models")
    def list_models():
        data = [
            {"id": m, "object": "model", "created": 0, "owned_by": "camerad"}
            for m in served
        ]
        return {"object": "list", "data": data}

    @app.get("/health")
    def health():
        return {"status": "ok", "models": served}

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        body = await request.json()
        messages = body.get("messages") or []
        if not messages:
            return JSONResponse(status_code=400, content={"error": "messages kosong"})
        model_req = body.get("model")
        max_new_tokens = body.get("max_tokens") or body.get("max_completion_tokens") or 512
        temperature = body.get("temperature")
        if temperature is None:
            temperature = 0.7
        text, p_tok, c_tok, used = _generate(
            messages, model_req, max_new_tokens, float(temperature)
        )
        return {
            "id": "chatcmpl-" + uuid.uuid4().hex,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model_req or used,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": p_tok,
                "completion_tokens": c_tok,
                "total_tokens": p_tok + c_tok,
            },
            "camerad_adapter": used,
        }

    return app


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.environ.get("VLLM_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("VLLM_PORT", "8001")))
    args = ap.parse_args()

    model, tokenizer, adapter_names, has_adapters = _load_model()
    print("[serve_local] model tersedia: %s" % ([TC.BASE_MODEL] + adapter_names), flush=True)
    app = build_app(model, tokenizer, adapter_names, has_adapters)

    import uvicorn
    print("[serve_local] listening di http://%s:%d/v1" % (args.host, args.port), flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
