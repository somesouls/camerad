#!/usr/bin/env bash
# Arsipkan skrip one-off yang sudah selesai tugasnya (reorg/migrasi/patch historis)
# ke scripts/_archive/. Aman: pakai 'git mv' sehingga riwayat tetap utuh.
#   bash scripts/archive_oneoffs.sh            -> PREVIEW
#   bash scripts/archive_oneoffs.sh --apply    -> pindahkan + commit lokal
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"; cd "$ROOT"
DEST="scripts/_archive"

ITEMS=(
  scripts/fix_banner_cosmetic.sh
  scripts/fix_base_awe_endif.sh
  scripts/fix_db_basedir.sh
  scripts/migrate_flatten.sh
  scripts/prune_shims.sh
  scripts/reorg_avaya_patches.sh
  scripts/reorg_awe_patch.sh
  scripts/reorg_chat.sh
  scripts/reorg_common.sh
  scripts/reorg_db.sh
  scripts/reorg_df_webhook.sh
  scripts/reorg_docs.sh
  scripts/reorg_handoff.sh
  scripts/reorg_install_oneoff.sh
  scripts/reorg_knowledge.sh
  scripts/reorg_pipeline_after_scaffold.sh
  scripts/reorg_pipeline_resume_windows.sh
  scripts/reorg_pkg.sh
  scripts/reorg_pkg_as.sh
  scripts/reorg_pkg_soft.sh
  scripts/reorg_pkg_sop.sh
  scripts/reorg_pustaka.sh
  scripts/reorg_rag_patches.sh
  scripts/reorg_routes.sh
  scripts/reorg_scripts.sh
  scripts/reorg_step_patches.sh
  scripts/oneoff/fix_awe_deflection.py
  scripts/oneoff/fix_awe_ikhtisar.py
  scripts/oneoff/fix_banner_cosmetic.py
  scripts/oneoff/fix_knowledge_match.py
  scripts/oneoff/fix_modal_canvas.py
  scripts/oneoff/fix_modal_transparan.py
  scripts/oneoff/fix_reindex_mismatch.py
  scripts/oneoff/fix_step10_report.py
  scripts/oneoff/fix_step5_opsiB.py
  scripts/oneoff/fix_step5_trainable.py
  scripts/oneoff/fix_step6_pill_opsiB.py
  scripts/oneoff/fix_step9.py
  scripts/oneoff/fix_step9_load_only.py
  scripts/oneoff/fix_step9_save.py
  scripts/oneoff/fix_tools_scope.py
  scripts/oneoff/fix_wire_audit_tp.py
  scripts/oneoff/migrate_flatten.py
  scripts/oneoff/prune_shims.py
  scripts/oneoff/reset_verdict_mkta.py
  scripts/oneoff/step10_build_new.py
)
# TETAP (tidak diarsipkan): scripts/oneoff/check_structure.py, install.py, cek_db.py

present=()
for f in "${ITEMS[@]}"; do
  [ -f "$f" ] && present+=("$f")
done

echo "Akan diarsipkan ke $DEST/ : ${#present[@]} berkas"
for f in "${present[@]}"; do echo "  $f"; done

if [ "${1:-}" != "--apply" ]; then
  echo ""
  echo "Untuk eksekusi: bash scripts/archive_oneoffs.sh --apply"
  exit 0
fi

if [ -n "$(git status --porcelain)" ]; then
  echo "ABORT: working tree tidak bersih. Commit/stash dulu."; exit 1
fi

mkdir -p "$DEST" "$DEST/oneoff"
for f in "${present[@]}"; do
  case "$f" in
    scripts/oneoff/*) git mv "$f" "$DEST/oneoff/$(basename "$f")" ;;
    *)                git mv "$f" "$DEST/$(basename "$f")" ;;
  esac
done

git commit --quiet -m "PR-24: arsipkan ${#present[@]} skrip one-off selesai ke scripts/_archive/"
echo ""
echo "OK: ${#present[@]} berkas dipindah ke $DEST/ dan di-commit (belum push)."
echo "Cek: git show --stat HEAD   | Batalkan: git reset --hard HEAD~1"
echo "Lalu: git push"
