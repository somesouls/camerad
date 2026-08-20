#!/usr/bin/env bash
# reorg_rag_patches.sh - PR-15: kelompokkan 8 patch RAG datar ke paket rag/.
# Pola sama seperti reorg_avaya_patches.sh: copy root -> rag/<sub>.py, root jadi
# shim (sys.modules aliasing). NOL rewrite (semua import internal tetap nama
# root-level yang resolve via shim yang sudah ada). Tidak menyentuh rag/__init__.py.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

# --- gerbang: working tree harus bersih -------------------------------------
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "ABORT: working tree tidak bersih. Commit/stash dulu."
  exit 1
fi

# --- autodetect runner python -----------------------------------------------
PY=""
for c in python py python3; do
  if command -v "$c" >/dev/null 2>&1; then
    if [ "$c" = "py" ]; then PY="py -3"; else PY="$c"; fi
    break
  fi
done
if [ -z "$PY" ]; then echo "ABORT: python tak ditemukan di PATH"; exit 1; fi
echo "Python runner: $PY"

PKG="rag"
PAIRS="rag_calibration_patch:calibration_patch rag_domain_patch:domain_patch rag_drilldown_patch:drilldown_patch rag_grounding_patch:grounding_patch rag_qa_patch:qa_patch rag_rerank_patch:rerank_patch rag_sources_patch:sources_patch rag_successor_patch:successor_patch"

# --- prasyarat: paket ada, sumber ada, target belum ada ---------------------
mkdir -p "$PKG"
[ -f "$PKG/__init__.py" ] || printf '' > "$PKG/__init__.py"

for pair in $PAIRS; do
  root="${pair%%:*}"; sub="${pair##*:}"
  if [ ! -f "$root.py" ]; then echo "ABORT: $root.py tak ditemukan"; exit 1; fi
  if [ -f "$PKG/$sub.py" ]; then echo "ABORT: $PKG/$sub.py sudah ada (tak menimpa)"; exit 1; fi
done

# --- pindahkan: copy asli ke paket ------------------------------------------
for pair in $PAIRS; do
  root="${pair%%:*}"; sub="${pair##*:}"
  cp "$root.py" "$PKG/$sub.py"
done

# --- root -> shim ------------------------------------------------------------
for pair in $PAIRS; do
  root="${pair%%:*}"; sub="${pair##*:}"
  cat > "$root.py" <<EOF
# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-15). Asli dipindah ke $PKG/$sub.py.
# import $root / from $root import ... tetap jalan sampai pemanggil diperbarui
# ke: from $PKG import $sub  (dibersihkan di PR akhir).
import sys as _sys
import $PKG.$sub as _mod
_sys.modules[__name__] = _mod
EOF
done

# --- gerbang py_compile ------------------------------------------------------
$PY -m py_compile \
  rag/calibration_patch.py rag/domain_patch.py rag/drilldown_patch.py rag/grounding_patch.py \
  rag/qa_patch.py rag/rerank_patch.py rag/sources_patch.py rag/successor_patch.py \
  rag_calibration_patch.py rag_domain_patch.py rag_drilldown_patch.py rag_grounding_patch.py \
  rag_qa_patch.py rag_rerank_patch.py rag_sources_patch.py rag_successor_patch.py \
  web_app.py
echo "GATE OK: py_compile lulus."

# --- commit LOKAL ------------------------------------------------------------
git add rag/__init__.py \
  rag/calibration_patch.py rag/domain_patch.py rag/drilldown_patch.py rag/grounding_patch.py \
  rag/qa_patch.py rag/rerank_patch.py rag/sources_patch.py rag/successor_patch.py \
  rag_calibration_patch.py rag_domain_patch.py rag_drilldown_patch.py rag_grounding_patch.py \
  rag_qa_patch.py rag_rerank_patch.py rag_sources_patch.py rag_successor_patch.py
git commit -m "PR-15: kelompokkan patch RAG ke paket rag (calibration, domain, drilldown, grounding, qa, rerank, sources, successor)"

echo ""
echo "OK: commit PR-15 rag patches dibuat LOKAL (belum di-push)."
echo "Smoke: $PY -c \"import rag.calibration_patch, rag.domain_patch, rag.drilldown_patch, rag.grounding_patch, rag.qa_patch, rag.rerank_patch, rag.sources_patch, rag.successor_patch; import rag_calibration_patch, rag_domain_patch, rag_drilldown_patch, rag_grounding_patch, rag_qa_patch, rag_rerank_patch, rag_sources_patch, rag_successor_patch; print('RAG PATCH OK')\""
echo "Boot: $PY web_app.py -> pastikan 8 baris [rag_*_patch] muncul & indeks Q&A tetap 2645 vektor -> git push"
