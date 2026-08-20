# -*- coding: utf-8 -*-
"""
Bantu menjalankan vLLM (OpenAI-compatible) dengan dukungan LoRA.

vLLM melayani base model Qwen2.5-7B-Instruct di :8001 dan bisa memuat beberapa
LoRA adapter sekaligus lewat --lora-modules.

Pakai:
    python -m finetune.serve_vllm --print   # cetak perintah saja
    python -m finetune.serve_vllm           # jalankan vLLM

Lalu daftarkan sebagai provider lokal di .env:
    LLM_PROVIDER=local
    VLLM_BASE_URL=http://127.0.0.1:8001/v1
    VLLM_MODEL=Qwen/Qwen2.5-7B-Instruct   # atau nama LoRA module (mis. camerad-grounded)
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from finetune import common as C  # noqa: E402
from finetune import train_config as TC  # noqa: E402


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
                found.append((f"camerad-{name}", p))
    return found


def build_command(port, max_len, gpu_util):
    cmd = [
        "vllm", "serve", TC.BASE_MODEL,
        "--port", str(port),
        "--enable-lora",
        "--max-lora-rank", str(TC.LORA_R),
        "--max-model-len", str(max_len),
        "--gpu-memory-utilization", str(gpu_util),
        "--served-model-name", TC.BASE_MODEL,
    ]
    adapters = _discover_adapters()
    if adapters:
        cmd.append("--lora-modules")
        cmd += [f"{name}={path}" for name, path in adapters]
    return cmd, adapters


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("VLLM_PORT", "8001")))
    ap.add_argument("--max-len", type=int, default=TC.MAX_SEQ_LEN)
    ap.add_argument("--gpu-util", type=float,
                    default=float(os.environ.get("VLLM_GPU_UTIL", "0.90")))
    ap.add_argument("--print", action="store_true", dest="print_only",
                    help="Cetak perintah lalu keluar (tidak menjalankan).")
    args = ap.parse_args()

    cmd, adapters = build_command(args.port, args.max_len, args.gpu_util)
    print("[serve_vllm] adapter terdeteksi:", flush=True)
    for name, path in adapters:
        print(f"  - {name}  <-  {path}", flush=True)
    if not adapters:
        print("  (belum ada; jalankan training dulu)", flush=True)
    print("\n[serve_vllm] perintah:\n  " + " ".join(cmd) + "\n", flush=True)

    if args.print_only:
        return

    import subprocess
    subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
