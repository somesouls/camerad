#!/usr/bin/env bash
# reorg_pkg_sop.sh - PR-4 (pilot): paket modul cluster `sop` -> sop/ + shim mundur.
#
# Memindahkan sop_db/batch/files/routes.py -> sop/{db,batch,files,routes}.py
# via `git mv` (byte-exact), lalu meninggalkan SHIM tipis di path lama supaya
# `import sop_db` dll tetap jalan. Shim memakai alias sys.modules sehingga path
# lama & baru menunjuk SATU objek modul (tanpa duplikasi state/cache modul).
#
# sop_db & sop_batch menambatkan path via os.path.dirname(__file__); karena
# turun satu folder, _BASE_DIR ditambah satu dirname lagi agar sop.db &
# sop_uploads TETAP di root repo.
#
# Aman & reversibel: commit dibuat LOKAL (belum di-push). Batalkan dengan:
#   git reset --hard HEAD~1   &&   rm -rf sop
#
# self-heal CRLF (buang CR lalu jalankan ulang via bash, sekali):
if [ -z "${_DECR:-}" ]; then export _DECR=1; tr -d '\r' < "$0" | bash -s -- "$@"; exit $?; fi
set -eu

# 1) Guard lokasi & working tree bersih
[ -f web_app.py ] || { echo "ERROR: jalankan dari root repo (web_app.py tak ada)"; exit 1; }
[ -d .git ]       || { echo "ERROR: bukan root git repo (.git tak ada)"; exit 1; }
if [ -n "$(git status --porcelain)" ]; then
  echo "ERROR: working tree tidak bersih. Bereskan dulu (mis. git reset --hard HEAD && git clean -fd)"; exit 1
fi

# 2) Prasyarat: 4 sumber ada di root; paket sop/ belum ada
for f in sop_db.py sop_batch.py sop_files.py sop_routes.py; do
  [ -f "$f" ] || { echo "ERROR: $f tak ada di root (mungkin sudah dipindah?)"; exit 1; }
done
[ -e sop ] && { echo "ERROR: 'sop' sudah ada; batalkan agar tak menimpa."; exit 1; }

# 3) Buat paket sop/
mkdir sop
cat > sop/__init__.py <<'PY'
# -*- coding: utf-8 -*-
"""Paket cluster SOP / Proses Bisnis (PR-4 reorg).

Modul: db, batch, files, routes. Impor lama (import sop_db dll) masih
didukung sementara lewat shim di root (dihapus pada PR pembersihan akhir).
"""
PY

# 4) Pindah byte-exact
git mv sop_db.py     sop/db.py
git mv sop_batch.py  sop/batch.py
git mv sop_files.py  sop/files.py
git mv sop_routes.py sop/routes.py

# 5) Tambatkan _BASE_DIR ke root repo (naik satu level) - hanya db.py & batch.py
python - <<'PY'
import io
OLD = "_BASE_DIR = os.path.dirname(os.path.abspath(__file__))"
NEW = "_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))"
for p in ("sop/db.py", "sop/batch.py"):
    s = io.open(p, encoding="utf-8").read()
    n = s.count(OLD)
    assert n == 1, "%s: penanda _BASE_DIR muncul %d kali (harus 1)" % (p, n)
    io.open(p, "w", encoding="utf-8", newline="\n").write(s.replace(OLD, NEW, 1))
    print("PATCH OK:", p)
PY

# 6) Shim mundur di root (alias sys.modules -> objek modul sama)
cat > sop_db.py <<'PY'
# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke sop/db.py.
# import sop_db / from sop_db import ... tetap jalan sampai pemanggil
# diperbarui ke: from sop import db  (dibersihkan di PR akhir).
import sys as _sys
import sop.db as _mod
_sys.modules[__name__] = _mod
PY
cat > sop_batch.py <<'PY'
# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke sop/batch.py.
import sys as _sys
import sop.batch as _mod
_sys.modules[__name__] = _mod
PY
cat > sop_files.py <<'PY'
# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke sop/files.py.
import sys as _sys
import sop.files as _mod
_sys.modules[__name__] = _mod
PY
cat > sop_routes.py <<'PY'
# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke sop/routes.py.
import sys as _sys
import sop.routes as _mod
_sys.modules[__name__] = _mod
PY

# 7) Stage + gerbang kompilasi (tangkap kerusakan sebelum commit)
git add -A sop sop_db.py sop_batch.py sop_files.py sop_routes.py
if ! python -m py_compile sop/db.py sop/batch.py sop/files.py sop/routes.py \
                          sop_db.py sop_batch.py sop_files.py sop_routes.py; then
  echo "ERROR: py_compile gagal -> membatalkan perubahan."
  git reset -q --hard HEAD
  rm -rf sop
  exit 1
fi

# 8) Commit LOKAL
git commit -q -m "refactor(sop): paket sop_* -> sop/ + shim mundur (PR-4 pilot)"
echo ""
echo "OK: commit PR-4 (sop) dibuat LOKAL (belum di-push)."
echo "LANGKAH BERIKUT:"
echo "  1) Boot test :  python web_app.py     (pastikan boot + banner [..._patch] muncul)"
echo "  2) Data utuh :  buka /sop atau GET /api/sop/stats -> total_dokumen & total_unit SAMA seperti sebelum"
echo "  3) Bila OK   :  git push"
echo "  4) Bila gagal:  git reset --hard HEAD~1  &&  rm -rf sop     (lalu lapor)"
