#!/usr/bin/env bash
# reorg_common.sh — PR-11: pindahkan utilitas bersama ke paket common/
# text_norm, pii_mask, regref, llm_client, intent_describe -> common/*
# root dipertahankan sebagai shim (import <mod> & from <mod> import x tetap jalan).
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"; cd "$ROOT"

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "ABORT: working tree tidak bersih. Commit/stash dulu."; exit 1
fi

PY=""
for c in python py python3; do
  if command -v "$c" >/dev/null 2>&1; then
    if [ "$c" = "py" ]; then PY="py -3"; else PY="$c"; fi
    break
  fi
done
[ -n "$PY" ] || { echo "ABORT: python tidak ditemukan di PATH"; exit 1; }
echo "Python runner: $PY"

for f in text_norm.py pii_mask.py regref.py llm_client.py intent_describe.py; do
  [ -f "$f" ] || { echo "ABORT: berkas sumber $f tidak ada"; exit 1; }
done

mkdir -p common

cat > common/__init__.py <<'PY'
# -*- coding: utf-8 -*-
"""paket common: utilitas bersama lintas fitur.

Berisi: text_norm, pii_mask, regref, llm_client, intent_describe.
Modul lama di root dipertahankan sebagai shim (kompatibilitas import).
"""
PY

cp text_norm.py       common/text_norm.py
cp pii_mask.py        common/pii_mask.py
cp regref.py          common/regref.py
cp llm_client.py      common/llm_client.py
cp intent_describe.py common/intent_describe.py

$PY - <<'PY'
from pathlib import Path
p = Path("common/intent_describe.py")
s = p.read_text(encoding="utf-8")
old = "    import llm_client as _llm"
new = "    from common import llm_client as _llm"
assert old in s, "ABORT: pola 'import llm_client as _llm' di common/intent_describe.py tak ditemukan"
s = s.replace(old, new)
p.write_text(s, encoding="utf-8")
print("IMPORT PATCH OK: common/intent_describe.py memakai from common import llm_client (intentmap_db tetap flat)")
PY

for m in text_norm pii_mask regref llm_client intent_describe; do
cat > "$m.py" <<PY
import sys as _sys
import common.$m as _mod
_sys.modules[__name__] = _mod
PY
done

$PY -m py_compile \
  common/__init__.py common/text_norm.py common/pii_mask.py common/regref.py common/llm_client.py common/intent_describe.py \
  text_norm.py pii_mask.py regref.py llm_client.py intent_describe.py \
  web_app.py app_core.py
echo "GATE OK: py_compile lulus."

git add common/__init__.py common/text_norm.py common/pii_mask.py common/regref.py common/llm_client.py common/intent_describe.py \
        text_norm.py pii_mask.py regref.py llm_client.py intent_describe.py
git commit -m "PR-11: pindahkan utilitas bersama ke paket common (text_norm, pii_mask, regref, llm_client, intent_describe)"

echo
echo "OK: commit PR-11 common dibuat LOKAL (belum di-push)."
echo 'Smoke: python -c "import common.text_norm, common.pii_mask, common.regref, common.llm_client, common.intent_describe; import text_norm, pii_mask, regref, llm_client, intent_describe; print(\"COMMON OK\")"'
echo "Boot: python web_app.py -> cek / + /livechat + /intentmap + /glossary 200 OK -> git push"
