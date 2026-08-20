#!/usr/bin/env bash
# reorg_scripts.sh -- arsipkan skrip sekali-jalan ke scripts/oneoff/ HANYA bila tak ada .py lain yang meng-import-nya.
# Pakai dari ROOT repo:  bash scripts/reorg_scripts.sh   lalu:  git push
# Runtime patch (step9_patch, step10_patch, *_patch), CLI aktif (phase*), dan entrypoint TIDAK disentuh.

if [ -z "${_DECR:-}" ]; then export _DECR=1; tr -d '\r' < "$0" | bash -s -- "$@"; exit $?; fi

set -eu
[ -f web_app.py ] || { echo "ERROR: jalankan dari ROOT repo." >&2; exit 1; }
[ -d .git ] || { echo "ERROR: bukan root repo git." >&2; exit 1; }

git reset -q

CANDIDATES="fix_awe_deflection.py fix_awe_ikhtisar.py fix_knowledge_match.py fix_modal_canvas.py fix_modal_transparan.py fix_reindex_mismatch.py fix_step10_report.py fix_step5_opsiB.py fix_step5_trainable.py fix_step6_pill_opsiB.py fix_step9.py fix_step9_load_only.py fix_step9_save.py fix_tools_scope.py fix_wire_audit_tp.py cek_db.py reset_verdict_mkta.py step10_build_new.py"

mkdir -p scripts/oneoff
for f in $CANDIDATES; do
  [ -e "$f" ] || continue
  mod="${f%.py}"
  if grep -RIlqE "(^|[^A-Za-z0-9_.])(import[[:space:]]+${mod}([[:space:]]|,|$)|from[[:space:]]+${mod}[[:space:]])" --include='*.py' --exclude="$f" --exclude-dir=scripts --exclude-dir=__pycache__ --exclude-dir=_legacy --exclude-dir=_studio . ; then
    echo "SKIP  $f  (masih di-import berkas lain -> dibiarkan di root)"
  else
    if git mv -k -- "$f" scripts/oneoff/; then echo "MOVE  $f -> scripts/oneoff/"; fi
  fi
done

if git diff --cached --quiet; then
  echo "Tidak ada skrip yang dipindah."
else
  git commit -q -m "chore(scripts): arsipkan skrip sekali-jalan ke scripts/oneoff/ (grep-verified)"
  echo "OK: commit dibuat. Sekarang jalankan:  git push"
fi
echo "Catatan: untuk menjalankan skrip arsip, pakai:  python -m scripts.oneoff.<nama>"
