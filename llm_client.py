# -*- coding: utf-8 -*-
"""
Client LLM cloud (OpenAI / Google Gemini) sebagai pengganti Qwen lokal.

Provider dipilih lewat variabel di file .env:
    LLM_PROVIDER=openai   -> pakai OpenAI (default)
    LLM_PROVIDER=gemini   -> pakai Google Gemini
    LLM_PROVIDER=azure    -> pakai Microsoft Azure OpenAI

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
        kwargs = {"api_key": api_key}
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
        )

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
            f"LLM_PROVIDER tidak dikenal: '{provider}'. Pakai 'openai' atau 'gemini'."
        )

    print(f"[LLM] Provider={_provider} model={_model} siap.", flush=True)


def _generate_one(system, user, max_new_tokens, temperature):
    max_retries = int(_cfg("LLM_MAX_RETRIES", "4"))
    delay = 2.0
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            if _provider in ("openai", "azure", "azure_openai", "azureopenai"):
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
                delay = min(delay * 2, 30)
    raise RuntimeError(f"LLM gagal setelah {max_retries} percobaan: {last_err}")


def chat(messages, system=None, max_new_tokens=1024, temperature=0.4, model=None):
    """Chat multi-turn. `messages` = list[{role, content}] (role: user/assistant/system).
    Mengembalikan satu string balasan asisten.

    `model` (opsional) menimpa model/deployment default HANYA untuk panggilan ini.
    Bila kosong/None, tetap memakai model global dari .env (OPENAI_MODEL /
    AZURE_OPENAI_DEPLOYMENT / GEMINI_MODEL). Dipakai untuk wiring model per-profil
    (mis. agent memakai model lebih kuat, chatbot memakai model lebih cepat)."""
    init_client()
    use_model = (str(model).strip() if model else "") or _model
    sys_txt = system or ""
    conv = []
    for m in (messages or []):
        role = (m.get("role") or "user").lower()
        content = m.get("content") or ""
        if role == "system":
            sys_txt = (sys_txt + "\n" + content).strip()
            continue
        conv.append({"role": role, "content": content})

    max_retries = int(_cfg("LLM_MAX_RETRIES", "4"))
    delay = 2.0
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            if _provider in ("openai", "azure", "azure_openai", "azureopenai"):
                msgs = []
                if sys_txt:
                    msgs.append({"role": "system", "content": sys_txt})
                for m in conv:
                    r = m["role"] if m["role"] in ("user", "assistant") else "user"
                    msgs.append({"role": r, "content": m["content"]})
                req_kwargs = {
                    "model": use_model,
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
            model_obj = _client.GenerativeModel(
                use_model, system_instruction=(sys_txt or None)
            )
            contents = []
            for m in conv:
                grole = "model" if m["role"] == "assistant" else "user"
                contents.append({"role": grole, "parts": [m["content"]]})
            resp = model_obj.generate_content(
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
                delay = min(delay * 2, 30)
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
