#!/usr/bin/env bash
# reorg_docs.sh -- pindah dokumen ke docs/ + untrack berkas runtime (riwayat git terjaga via git mv).
# Pakai dari ROOT repo:  bash scripts/reorg_docs.sh   lalu:  git push
# Aman: 'git reset' dulu agar tidak ikut meng-commit berkas yang sudah Anda stage;
# lalu commit HANYA perubahan script ini. Berkas .db tetap di disk (hanya di-untrack).

# Self-heal CRLF: bila file ber-CR (checkout Windows), strip CR lalu jalankan ulang.
if [ -z "${_DECR:-}" ]; then export _DECR=1; tr -d '\r' < "$0" | bash -s -- "$@"; exit $?; fi

set -eu
[ -f web_app.py ] || { echo "ERROR: jalankan dari ROOT repo (web_app.py tak ada)." >&2; exit 1; }
[ -d .git ] || { echo "ERROR: bukan root repo git (.git tak ada)." >&2; exit 1; }

git reset -q

mkdir -p docs/changelog
for f in CHANGES_*.md; do
  [ -e "$f" ] || continue
  git mv -k -- "$f" docs/changelog/ || true
done

for f in INSTALL.txt KNOWLEDGE_MATCH_FIX.txt PANDUAN_CHATBOT_LOKAL_DIALOGFLOW.md; do
  [ -e "$f" ] || continue
  git mv -k -- "$f" docs/ || true
done

git rm -r --cached --ignore-unmatch --quiet __pycache__ >/dev/null 2>&1 || true
git rm --cached --ignore-unmatch --quiet peraturan.db-shm peraturan.db-wal >/dev/null 2>&1 || true

if git diff --cached --quiet; then
  echo "Tidak ada perubahan (mungkin sudah rapi)."
else
  git commit -q -m "chore(docs): pindah changelog & panduan ke docs/, untrack __pycache__ & *.db sidecar"
  echo "OK: commit dibuat. Sekarang jalankan:  git push"
fi
