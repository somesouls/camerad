#!/usr/bin/env bash
# Perbaiki base.html: tutup blok {% if can_awe %} yang kehilangan {% endif %}.
# Idempoten: kalau if/endif sudah seimbang, tidak melakukan apa-apa.
set -euo pipefail

ROOT=$(git rev-parse --show-toplevel)
cd "$ROOT"

# --- Autodetect python runner ---
PY=""
for c in python python3; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ] && command -v py >/dev/null 2>&1; then PY="py -3"; fi
if [ -z "$PY" ]; then echo "ABORT: python tidak ditemukan."; exit 1; fi
echo "Python runner: $PY"

if [ ! -f templates/base.html ]; then
  echo "ABORT: templates/base.html tidak ada."; exit 1
fi

$PY - <<'PYEOF'
import re, io
p = "templates/base.html"
s = io.open(p, encoding="utf-8").read()
n_if = len(re.findall(r"{%-?\s*if\b", s))
n_endif = len(re.findall(r"{%-?\s*endif\s*-?%}", s))
print(f"sebelum: if={n_if} endif={n_endif}")
if n_if == n_endif:
    print("BASE OK: if/endif sudah seimbang, tidak ada perubahan.")
else:
    diff = n_if - n_endif
    assert diff == 1, f"ABORT: ketidakseimbangan tak terduga (if={n_if} endif={n_endif}, diff={diff}); perlu cek manual."
    anchor = "{% endif %}\n        {% if can_sosmed %}"
    cnt = s.count(anchor)
    assert cnt == 1, f"ABORT: anchor Sosmed tidak unik (ketemu {cnt}x); perlu cek manual."
    s = s.replace(anchor, "{% endif %}\n        {% endif %}\n        {% if can_sosmed %}", 1)
    io.open(p, "w", encoding="utf-8", newline="\n").write(s)
    n_if2 = len(re.findall(r"{%-?\s*if\b", s))
    n_endif2 = len(re.findall(r"{%-?\s*endif\s*-?%}", s))
    print(f"BASE FIX: tambah {{% endif %}} penutup can_awe sebelum blok Sosmed. Sekarang if={n_if2} endif={n_endif2}.")
    assert n_if2 == n_endif2, "ABORT: setelah patch masih tidak seimbang."
PYEOF

# Gate: pastikan base.html bisa di-parse Jinja (kalau jinja2 tersedia)
$PY - <<'PYEOF'
try:
    from jinja2 import Environment, FileSystemLoader
except Exception as e:
    print(f"(lewati gate Jinja: {e})")
else:
    env = Environment(loader=FileSystemLoader("templates"))
    env.parse(io.__import__('io').open("templates/base.html", encoding="utf-8").read())
    print("GATE OK: base.html lolos parse Jinja.")
PYEOF

git add templates/base.html
if git diff --cached --quiet; then
  echo "OK: tidak ada perubahan (base.html sudah benar)."
else
  git commit -m "fix(base.html): tutup blok {% if can_awe %} yang hilang endif"
  echo "OK: commit fix base.html dibuat LOKAL (belum di-push)."
fi
echo "Boot: python web_app.py -> cek /handoff /glossary /disambig 200 OK -> git push"
