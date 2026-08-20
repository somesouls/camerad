#!/usr/bin/env bash
# reorg_routes.sh — PR-13: kelompokkan 7 feature route (hasil migrasi bertahap
# dari web_app.py) ke paket routes/. Root tetap jadi shim agar web_app.py yang
# memanggil `import X; X.register(app)` tetap jalan TANPA disentuh.
# NOL rewrite: semua import di dalamnya absolute top-level (app_core, analytics_db,
# users_db, intentmap_db, pii_mask, ingest, docstudio, pustaka_stats, pipeline_routes,
# knowledge_semantic, numpy, openpyxl) yang tetap resolve via shim root / paket
# masing-masing meski file pindah ke routes/. studio_routes.register(app, base_dir=...)
# menerima base_dir dari web_app.py (BUKAN __file__), jadi path _studio tak berubah.
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

MODS="analytics_routes audit_tp_routes auth_routes data_routes lifecycle_routes studio_routes system_routes"

for m in $MODS; do
  [ -f "$m.py" ] || { echo "ABORT: $m.py tidak ada di root"; exit 1; }
done

mkdir -p routes
cat > routes/__init__.py <<'PY'
# Paket routes: feature route FastAPI yang dulunya berkas datar di root
# (hasil migrasi bertahap dari web_app.py). Diimpor via shim root demi kompat.
PY

# Salin file asli ke paket routes/
for m in $MODS; do
  cp "$m.py" "routes/$m.py"
done

# Timpa root dengan shim (unquoted heredoc supaya $m mengembang)
for m in $MODS; do
  cat > "$m.py" <<PY
import sys as _sys
import routes.$m as _mod
_sys.modules[__name__] = _mod
PY
done

NEWFILES=""
for m in $MODS; do NEWFILES="$NEWFILES routes/$m.py $m.py"; done
$PY -m py_compile routes/__init__.py $NEWFILES web_app.py app_core.py
echo "GATE OK: py_compile lulus."

git add routes
for m in $MODS; do git add "$m.py"; done
git commit -m "PR-13: kelompokkan feature route ke paket routes (analytics, audit_tp, auth, data, lifecycle, studio, system)"

echo
echo "OK: commit PR-13 routes dibuat LOKAL (belum di-push)."
echo "Smoke: python -c \"import routes.analytics_routes, routes.audit_tp_routes, routes.auth_routes, routes.data_routes, routes.lifecycle_routes, routes.studio_routes, routes.system_routes; import analytics_routes, audit_tp_routes, auth_routes, data_routes, lifecycle_routes, studio_routes, system_routes; print('ROUTES OK')\""
echo "Boot: python web_app.py -> cek /dashboard /deflection /data /audit-tp /users /profil /lifecycle /studio /api/pustaka/stats /healthz 200 OK -> git push"
