#!/usr/bin/env bash
# reorg_pkg_sop.sh - PR-4 (pilot): paket cluster `sop` -> sop/ + shim mundur.
# v2: patch _BASE_DIR pakai sed (tanpa `python` di shell bash), gate py_compile best-effort,
#     + rollback OTOMATIS bila gagal di tengah (trap ERR).
#
# Pindah sop_db/batch/files/routes.py -> sop/{db,batch,files,routes}.py (git mv, byte-exact),
# tinggalkan SHIM tipis di root (alias sys.modules -> satu objek modul, tanpa duplikasi state).
# _BASE_DIR di db.py & batch.py dinaikkan satu level agar sop.db & sop_uploads TETAP di root repo.
#
# Reversibel: commit dibuat LOKAL (belum push). Batalkan setelah commit:
#   bash -c 'git reset --hard HEAD~1 && rm -rf sop'

# self-heal CRLF (buang CR lalu jalankan ulang via bash, sekali):
if [ -z "${_DECR:-}" ]; then export _DECR=1; tr -d '\r' < "$0" | bash -s -- "$@"; exit $?; fi
set -eu

# Samakan persepsi git di bash dgn PowerShell (mesin ini punya 2 setup git; repo-local, idempoten).
git config core.autocrlf true  2>/dev/null || true
git config core.fileMode false 2>/dev/null || true

command -v git >/dev/null || { echo "ERROR: git tak ada di shell ini"; exit 1; }
command -v sed >/dev/null || { echo "ERROR: sed tak ada di shell ini"; exit 1; }

# --- Guard (sebelum membuat apa pun) ---
[ -f web_app.py ] || { echo "ERROR: jalankan dari root repo (web_app.py tak ada)"; exit 1; }
[ -d .git ]       || { echo "ERROR: bukan root git repo (.git tak ada)"; exit 1; }
if [ -n "$(git status --porcelain)" ]; then
  echo "ERROR: working tree tidak bersih. Bereskan dulu:  bash -c 'git reset --hard HEAD && rm -rf sop'"; exit 1
fi
for f in sop_db.py sop_batch.py sop_files.py sop_routes.py; do
  [ -f "$f" ] || { echo "ERROR: $f tak ada di root (mungkin sudah dipindah?)"; exit 1; }
done
[ -e sop ] && { echo "ERROR: 'sop' sudah ada; batalkan agar tak menimpa."; exit 1; }

# --- Mulai mengubah: pasang jaring rollback otomatis ---
rollback() { echo ""; echo ">> GAGAL -> rollback otomatis ke kondisi bersih."; git reset -q --hard HEAD 2>/dev/null || true; rm -rf sop 2>/dev/null || true; }
trap 'rollback' ERR

# 1) Buat paket sop/
mkdir sop
cat > sop/__init__.py <<'PY'
# -*- coding: utf-8 -*-
"""Paket cluster SOP / Proses Bisnis (PR-4 reorg).

Modul: db, batch, files, routes. Impor lama (import sop_db dll) masih
didukung sementara lewat shim di root (dihapus pada PR pembersihan akhir).
"""
PY

# 2) Pindah byte-exact
git mv sop_db.py     sop/db.py
git mv sop_batch.py  sop/batch.py
git mv sop_files.py  sop/files.py
git mv sop_routes.py sop/routes.py

# 3) Tambatkan _BASE_DIR ke root repo (naik satu level) - hanya db.py & batch.py
OLD='_BASE_DIR = os.path.dirname(os.path.abspath(__file__))'
NEWMARK='_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))'
for f in sop/db.py sop/batch.py; do
  n=$(grep -Fc "$OLD" "$f" || true)
  [ "$n" = "1" ] || { echo "ERROR: penanda _BASE_DIR di $f = $n (harus 1)."; rollback; trap - ERR; exit 1; }
  sed -i 's|_BASE_DIR = os\.path\.dirname(os\.path\.abspath(__file__))|_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))|' "$f"
  grep -Fq "$NEWMARK" "$f" || { echo "ERROR: patch _BASE_DIR di $f gagal."; rollback; trap - ERR; exit 1; }
  echo "PATCH OK: $f"
done

# 4) Shim mundur di root (alias sys.modules -> objek modul sama)
for m in db batch files routes; do
  cat > "sop_${m}.py" <<PY
# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke sop/${m}.py.
# import sop_${m} / from sop_${m} import ... tetap jalan sampai pemanggil
# diperbarui ke: from sop import ${m}  (dibersihkan di PR akhir).
import sys as _sys
import sop.${m} as _mod
_sys.modules[__name__] = _mod
PY
done

# 5) Stage (scoped) + gerbang py_compile best-effort
git add -A sop sop_db.py sop_batch.py sop_files.py sop_routes.py
PYBIN=""
for c in python3 python py; do command -v "$c" >/dev/null 2>&1 && { PYBIN="$c"; break; }; done
if [ -n "$PYBIN" ]; then
  echo "Gate: py_compile ($PYBIN)..."
  "$PYBIN" -m py_compile sop/db.py sop/batch.py sop/files.py sop/routes.py sop_db.py sop_batch.py sop_files.py sop_routes.py
  echo "GATE OK: py_compile lulus."
else
  echo "CATATAN: 'python' tak ada di shell ini -> py_compile dilewati; WAJIB boot-test dari PowerShell (.venv)."
fi

# 6) Commit LOKAL; lepas jaring rollback
git commit -q -m "refactor(sop): paket sop_* -> sop/ + shim mundur (PR-4 pilot)"
trap - ERR
echo ""
echo "OK: commit PR-4 (sop) dibuat LOKAL (belum di-push)."
echo "LANGKAH BERIKUT (di PowerShell, venv aktif):"
echo "  1) Tutup web_app lama (port 8080), lalu boot test:  python web_app.py"
echo "  2) Cek data utuh:  buka /sop atau GET /api/sop/stats -> total_dokumen & total_unit SAMA"
echo "  3) Bila OK   :  git push"
echo "  4) Bila gagal:  bash -c 'git reset --hard HEAD~1 && rm -rf sop'   (lalu lapor)"
