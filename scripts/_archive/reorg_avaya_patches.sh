#!/usr/bin/env bash
# reorg_avaya_patches.sh — PR-14: pindahkan 3 patch Avaya yang masih datar di
# root ke paket avaya/ (yang sudah eksis: __init__, client, db, pipeline).
#   avaya_dashpatch.py      -> avaya/dashpatch.py
#   avaya_speedpatch.py     -> avaya/speedpatch.py
#   avaya_web_bootstrap.py  -> avaya/web_bootstrap.py
# Root tetap jadi shim. NOL rewrite: semua referensi internal (avaya_pipeline,
# llm_client, avaya_speedpatch, avaya_dashpatch) tetap resolve via shim root.
# app_core.py meng-import avaya_web_bootstrap -> tetap jalan lewat shim, tak disentuh.
# TIDAK menimpa avaya/__init__.py yang sudah ada.
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

# pasangan "modul_root:submodul"
PAIRS="avaya_dashpatch:dashpatch avaya_speedpatch:speedpatch avaya_web_bootstrap:web_bootstrap"

[ -d avaya ] || { echo "ABORT: paket avaya/ belum ada"; exit 1; }
[ -f avaya/__init__.py ] || { echo "ABORT: avaya/__init__.py tidak ada (paket rusak?)"; exit 1; }

for pair in $PAIRS; do
  root="${pair%%:*}"; sub="${pair##*:}"
  [ -f "$root.py" ] || { echo "ABORT: $root.py tidak ada di root"; exit 1; }
  [ -e "avaya/$sub.py" ] && { echo "ABORT: avaya/$sub.py sudah ada, batal biar tak menimpa"; exit 1; }
done

# Salin ke paket
for pair in $PAIRS; do
  root="${pair%%:*}"; sub="${pair##*:}"
  cp "$root.py" "avaya/$sub.py"
done

# Root -> shim
for pair in $PAIRS; do
  root="${pair%%:*}"; sub="${pair##*:}"
  cat > "$root.py" <<PY
import sys as _sys
import avaya.$sub as _mod
_sys.modules[__name__] = _mod
PY
done

NEWFILES=""
for pair in $PAIRS; do
  root="${pair%%:*}"; sub="${pair##*:}"
  NEWFILES="$NEWFILES avaya/$sub.py $root.py"
done
$PY -m py_compile $NEWFILES web_app.py app_core.py
echo "GATE OK: py_compile lulus."

for pair in $PAIRS; do
  root="${pair%%:*}"; sub="${pair##*:}"
  git add "avaya/$sub.py" "$root.py"
done
git commit -m "PR-14: kelompokkan patch Avaya ke paket avaya (dashpatch, speedpatch, web_bootstrap)"

echo
echo "OK: commit PR-14 avaya patches dibuat LOKAL (belum di-push)."
echo "Smoke: python -c \"import avaya.dashpatch, avaya.speedpatch, avaya.web_bootstrap; import avaya_dashpatch, avaya_speedpatch, avaya_web_bootstrap; print('AVAYA PATCH OK')\""
echo "Boot: python web_app.py -> pastikan log '[AVAYA-SPEED] v2 terpasang', '[AVAYA-DASH] terpasang', '[AVAYA-WEB] speedpatch aktif', '[AVAYA-WEB] dashpatch aktif', '[AVAYA-WEB] route Avaya aktif' muncul & dashboard Avaya 200 OK -> git push"
