#!/usr/bin/env bash
# reorg_docs.sh — Langkah 2 refactor: pindah dokumen ke docs/ (riwayat git TERJAGA).
#
# Jalankan dari ROOT repo:
#   bash scripts/reorg_docs.sh
#
# AMAN: hanya memindah dokumen & untrack __pycache__. TIDAK menyentuh kode runtime.
# Pakai 'git mv' agar 'git log --follow' tetap bisa menelusuri riwayat berkas.
set -euo pipefail

# Pastikan dijalankan dari root repo (ada web_app.py)
if [ ! -f web_app.py ]; then
  echo "ERROR: jalankan dari ROOT repo (tidak menemukan web_app.py)." >&2
  exit 1
fi

mkdir -p docs/changelog

# 1) Changelog: CHANGES_*.md -> docs/changelog/
if ls CHANGES_*.md >/dev/null 2>&1; then
  git mv CHANGES_*.md docs/changelog/
fi

# 2) Panduan & catatan lepas -> docs/  (README.md sengaja TETAP di root)
for f in INSTALL.txt KNOWLEDGE_MATCH_FIX.txt PANDUAN_CHATBOT_LOKAL_DIALOGFLOW.md; do
  if [ -f "$f" ]; then git mv "$f" docs/; fi
done

# 3) Untrack __pycache__ yang terlanjur ter-commit (sudah ada di .gitignore)
git rm -r --cached __pycache__ >/dev/null 2>&1 || true

echo ""
echo "Selesai. Tinjau dengan 'git status', lalu commit:"
echo "  git commit -m 'chore(docs): pindah changelog & panduan ke docs/ + untrack __pycache__'"
