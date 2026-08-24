# -*- coding: utf-8 -*-
"""
Client LLM cloud (OpenAI / Google Gemini) sebagai pengganti Qwen lokal.

Provider dipilih lewat variabel di file .env:
    LLM_PROVIDER=openai   -> pakai OpenAI (default)
    LLM_PROVIDER=gemini   -> pakai Google Gemini
    LLM_PROVIDER=azure    -> pakai Microsoft Azure OpenAI
    LLM_PROVIDER=local    -> pakai server lokal OpenAI-compatible (mis. vLLM)

Fungsi utama:
    init_client()                      -> inisialisasi + validasi kunci API
    generate(prompts, max_new_tokens,  -> list[str] hasil untuk tiap prompt
             system=None, temperature=0.0)

Antarmuka generate() sengaja dibuat menerima list prompt agar drop-in
menggantikan pemanggilan batch Qwen yang lama.
"""
import os
import time

_client = None
_provider = None
_model = None


def _cfg(name, default=None):
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _timeout():
    """Timeout keras request LLM.

    Tanpa timeout eksplisit, OpenAI SDK bisa menunggu lama sekali pada koneksi
    lokal/proxy yang mati. Dampaknya pada /livechat: job Opsi B baru selesai
    ratusan detik kemudian, frontend terus polling, dan log terlihat looping.
    Default dibuat pendek supaya gagal cepat lalu fallback; bisa dioverride via
    LLM_TIMEOUT_SECONDS bila provider memang lambat.
    """
    try:
        return float(_cfg("LLM_TIMEOUT_SECONDS", "25") or "25")
    except Exception:
        return 25.0


def init_client():
    """Inisialisasi sekali; aman dipanggil berkali-kali."""
    global _client, _provider, _model
    if _client is not None:
        return

    provider = (_cfg("LLM_PROVIDER", "openai") or "").strip().lower()
    _provider = provider

    if provider == "openai":
        from openai import OpenAI
        api_key = _cfg("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY belum diisi di .env (LLM_PROVIDER=openai)."
            )
        _model = _cfg("OPENAI_MODEL", "gpt-4o-mini")
        kwargs = {"api_key": api_key, "timeout": _timeout(), "max_retries": 0}
        base_url = _cfg("OPENAI_BASE_URL")
        if base_url:
            kwargs["base_url"] = base_url  # untuk Azure/OpenAI-compatible proxy
        _client = OpenAI(**kwargs)

    elif provider in ("azure", "azure_openai", "azureopenai"):
        from openai import AzureOpenAI
        api_key = _cfg("AZURE_OPENAI_API_KEY")
        endpoint = _cfg("AZURE_OPENAI_ENDPOINT")
        if not api_key or not endpoint:
            raise RuntimeError(
                "AZURE_OPENAI_API_KEY / AZURE_OPENAI_ENDPOINT belum diisi di .env "
                "(LLM_PROVIDER=azure)."
            )
        # Azure OpenAI client expects the resource endpoint without '/openai' or
        # '/openai/v1' path segments.
        endpoint = endpoint.rstrip('/')
        if endpoint.lower().endswith('/openai/v1'):
            endpoint = endpoint[: -len('/openai/v1')]
        elif endpoint.lower().endswith('/openai'):
            endpoint = endpoint[: -len('/openai')]
        if endpoint.endswith('/'):
            endpoint = endpoint.rstrip('/')
        # Pada Azure, 'model' = nama DEPLOYMENT Anda, bukan nama model mentah.
        _model = _cfg("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")
        print(f"[LLM azure] normalized endpoint={endpoint} deployment={_model}", flush=True)
        _client = AzureOpenAI(
            api_key=api_key,
            azure_endpoint=endpoint,
            api_version=_cfg("AZURE_OPENAI_API_VERSION", "2024-06-01"),
            timeout=_timeout(),
            max_retries=0,
        )

    elif provider in ("local", "vllm"):
        # Server lokal OpenAI-compatible (vLLM / TGI / llama.cpp server).
        # Dipakai untuk menyajikan Qwen2.5-7B-Instruct + LoRA adapter camerad.
        from openai import OpenAI
        base_url = _cfg("VLLM_BASE_URL") or _cfg(
            "LOCAL_LLM_BASE_URL", "http://127.0.0.1:8001/v1"
        )
        # vLLM tidak memvalidasi API key, tapi klien OpenAI butuh string non-kosong.
        api_key = _cfg("VLLM_API_KEY") or _cfg("LOCAL_LLM_API_KEY", "sk-local")
        # _model bisa berupa nama base model ATAU nama LoRA module
        # (mis. 'camerad-grounded') yang didaftarkan vLLM lewat --lora-modules.
        _model = _cfg("VLLM_MODEL") or _cfg(
            "LOCAL_LLM_MODEL", "Qwen/Qwen2.5-7B-Instruct"
        )
        _client = OpenAI(api_key=api_key, base_url=base_url, timeout=_timeout(), max_retries=0)

    elif provider in ("gemini", "google"):
        import google.generativeai as genai
        api_key = _cfg("GEMINI_API_KEY") or _cfg("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY belum diisi di .env (LLM_PROVIDER=gemini)."
            )
        genai.configure(api_key=api_key)
        _model = _cfg("GEMINI_MODEL", "gemini-1.5-flash")
        _client = genai

    else:
        raise RuntimeError(
            f"LLM_PROVIDER tidak dikenal: '{provider}'. "
            "Pakai 'openai', 'azure', 'gemini', atau 'local' (vLLM)."
        )

    print(f"[LLM] Provider={_provider} model={_model} timeout={_timeout()}s siap.", flush=True)


def _max_retries():
    # Default lama 4 membuat satu request rusak bisa menggantung lama sekali.
    # Untuk livechat lebih baik fail-fast; override jika perlu.
    try:
        return max(1, int(_cfg("LLM_MAX_RETRIES", "1") or "1"))
    except Exception:
        return 1


def _generate_one(system, user, max_new_tokens, temperature):
    max_retries = _max_retries()
    delay = 1.0
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            if _provider in ("openai", "azure", "azure_openai", "azureopenai", "local", "vllm"):
                messages = []
                if system:
                    messages.append({"role": "system", "content": system})
                messages.append({"role": "user", "content": user})
                req_kwargs = {
                    "model": _model,
                    "messages": messages,
                    "temperature": temperature,
                }
                if _provider in ("azure", "azure_openai", "azureopenai"):
                    req_kwargs["max_completion_tokens"] = max(int(max_new_tokens), 1)
                else:
                    req_kwargs["max_tokens"] = max(int(max_new_tokens), 1)
                resp = _client.chat.completions.create(**req_kwargs)
                return (resp.choices[0].message.content or "").strip()

            # gemini / google
            model = _client.GenerativeModel(
                _model,
                system_instruction=(system or None),
            )
            resp = model.generate_content(
                user,
                generation_config={
                    "max_output_tokens": max(int(max_new_tokens), 16),
                    "temperature": temperature,
                },
            )
            return (getattr(resp, "text", "") or "").strip()

        except Exception as exc:  # noqa: BLE001
            last_err = exc
            print(f"[LLM] percobaan {attempt}/{max_retries} gagal: {exc}",
                  flush=True)
            if attempt < max_retries:
                time.sleep(delay)
                delay = min(delay * 2, 10)
    raise RuntimeError(f"LLM gagal setelah {max_retries} percobaan: {last_err}")


def chat(messages, system=None, max_new_tokens=1024, temperature=0.4):
    """Chat multi-turn. `messages` = list[{role, content}] (role: user/assistant/system).
    Mengembalikan satu string balasan asisten."""
    init_client()
    sys_txt = system or ""
    conv = []
    for m in (messages or []):
        role = (m.get("role") or "user").lower()
        content = m.get("content") or ""
        if role == "system":
            sys_txt = (sys_txt + "\n" + content).strip()
            continue
        conv.append({"role": role, "content": content})

    max_retries = _max_retries()
    delay = 1.0
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            if _provider in ("openai", "azure", "azure_openai", "azureopenai", "local", "vllm"):
                msgs = []
                if sys_txt:
                    msgs.append({"role": "system", "content": sys_txt})
                for m in conv:
                    r = m["role"] if m["role"] in ("user", "assistant") else "user"
                    msgs.append({"role": r, "content": m["content"]})
                req_kwargs = {
                    "model": _model,
                    "messages": msgs,
                    "temperature": temperature,
                }
                if _provider in ("azure", "azure_openai", "azureopenai"):
                    req_kwargs["max_completion_tokens"] = max(int(max_new_tokens), 1)
                else:
                    req_kwargs["max_tokens"] = max(int(max_new_tokens), 1)
                resp = _client.chat.completions.create(**req_kwargs)
                return (resp.choices[0].message.content or "").strip()

            # gemini / google
            model = _client.GenerativeModel(
                _model, system_instruction=(sys_txt or None)
            )
            contents = []
            for m in conv:
                grole = "model" if m["role"] == "assistant" else "user"
                contents.append({"role": grole, "parts": [m["content"]]})
            resp = model.generate_content(
                contents,
                generation_config={
                    "max_output_tokens": max(int(max_new_tokens), 16),
                    "temperature": temperature,
                },
            )
            return (getattr(resp, "text", "") or "").strip()

        except Exception as exc:  # noqa: BLE001
            last_err = exc
            print(f"[LLM chat] percobaan {attempt}/{max_retries} gagal: {exc}",
                  flush=True)
            if attempt < max_retries:
                time.sleep(delay)
                delay = min(delay * 2, 10)
    raise RuntimeError(f"LLM chat gagal setelah {max_retries} percobaan: {last_err}")


def generate(prompts, max_new_tokens=256, system=None, temperature=0.0):
    """Kembalikan list output (satu string per prompt)."""
    init_client()
    if isinstance(prompts, str):
        prompts = [prompts]
    return [
        _generate_one(system, p, max_new_tokens, temperature)
        for p in prompts
    ]
