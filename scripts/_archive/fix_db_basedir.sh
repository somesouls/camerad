#!/usr/bin/env bash
# fix_db_basedir.sh — HOTFIX PR-12.
# Setelah analytics_db.py & qa_index_db.py pindah ke paket db/, konstanta
# _BASE_DIR (dipakai default_db_path) jadi menunjuk folder db/, sehingga
# analytics.db & qa.db DEFAULT dicari di db/ (kosong) — bukan di root repo.
# Akibat: '[rag_qa_patch] ... indeks Q&A: BELUM ADA vektor' & dashboard kosong.
# Perbaikan: anchor _BASE_DIR ke ROOT repo (parent dari paket db/).
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

for f in db/analytics_db.py db/qa_index_db.py; do
  [ -f "$f" ] || { echo "ABORT: $f tidak ada (pastikan PR-12 sudah diterapkan)"; exit 1; }
done

$PY - <<'PY'
from pathlib import Path
old = "_BASE_DIR = os.path.dirname(os.path.abspath(__file__))"
new = "_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # paket db/ -> root repo"
for fn in ("db/analytics_db.py", "db/qa_index_db.py"):
    p = Path(fn)
    s = p.read_text(encoding="utf-8")
    if "root repo" in s:
        print("SKIP (sudah diperbaiki):", fn)
        continue
    assert old in s, "ABORT: pola _BASE_DIR tak ditemukan di %s" % fn
    s = s.replace(old, new, 1)
    p.write_text(s, encoding="utf-8")
    print("FIX OK:", fn, "-> _BASE_DIR anchor ke root repo")
PY

$PY -m py_compile db/analytics_db.py db/qa_index_db.py
echo "GATE OK: py_compile lulus."

git add db/analytics_db.py db/qa_index_db.py
git commit -m "fix(db): anchor _BASE_DIR ke root repo agar analytics.db & qa.db default tetap ditemukan pasca-PR-12"

echo
echo "OK: commit hotfix dibuat LOKAL (belum di-push)."
echo "Bersihkan DB kosong salah-tempat (opsional): hapus db/qa.db* dan db/analytics.db* bila ada."
echo "Boot ulang: python web_app.py -> pastikan '[rag_qa_patch] ... indeks Q&A: <N> vektor' (BUKAN BELUM ADA) & /dashboard ada datanya -> git push"
