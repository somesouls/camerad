# -*- coding: utf-8 -*-
"""
fix_knowledge_match.py
----------------------
Membuat pencocokan pustaka pengetahuan (glosarium, disambiguasi, peta intent)
lebih tahan-banting untuk utterance nyata:
  1. disambig.match  : dari 'substring persis' -> token-aware (samakan dgn 2 pustaka lain)
  2. ketiganya       : toleran salah ketik ringan (mis. 'pasword' ~ 'password')
                       via difflib pada token >= 5 huruf (cutoff 0.84).

Jalankan di folder yang memuat glossary_db.py, disambig_db.py, intentmap_db.py:
    python fix_knowledge_match.py
Aman diulang (mendeteksi bila sudah dipatch), membuat .bak, dan py_compile.
"""
import io, os, sys, py_compile

HELPER_KV = (
    "import difflib as _difflib\n\n\n"
    "def _tok_in(tok, qtok):\n"
    "    if tok in qtok:\n"
    "        return True\n"
    "    if len(tok) >= 5:\n"
    "        return bool(_difflib.get_close_matches(tok, list(qtok), n=1, cutoff=0.84))\n"
    "    return False\n\n\n"
    "def _key_hit(key, ql, qtok):\n"
)

DIS_HELPER = (
    "import difflib as _difflib\n\n\n"
    "_STOP = set(\n"
    '    "saya aku kami mau ingin gimana bagaimana cara kok ya dan atau di ke dari "\n'
    '    "yang untuk apa apakah tolong min pak bu nya dong sih itu ini dulu lama sudah "\n'
    '    "tidak gak ga tak lagi ada mohon bisa".split()\n'
    ")\n\n\n"
    "def _tokens(s):\n"
    '    return [t for t in re.findall(r"[a-z0-9]+", (s or "").lower())\n'
    "            if t not in _STOP and len(t) > 1]\n\n\n"
    "def _tok_in(tok, qtok):\n"
    "    if tok in qtok:\n"
    "        return True\n"
    "    if len(tok) >= 5:\n"
    "        return bool(_difflib.get_close_matches(tok, list(qtok), n=1, cutoff=0.84))\n"
    "    return False\n\n\n"
    "def _key_hit(key, ql, qtok):\n"
    "    key = (key or \"\").strip().lower()\n"
    "    if not key:\n"
    "        return False\n"
    "    if key in ql:\n"
    "        return True\n"
    "    ktok = _tokens(key)\n"
    "    if not ktok:\n"
    "        return False\n"
    "    hit = sum(1 for t in ktok if _tok_in(t, qtok))\n"
    "    if len(ktok) == 1:\n"
    "        return hit == 1\n"
    "    return (hit / len(ktok)) >= 0.6 and hit >= 2\n\n\n"
    "def match(conn, query, tanggal=None, limit=5):\n"
)

HIT_OLD = "    hit = sum(1 for t in ktok if t in qtok)"
HIT_NEW = "    hit = sum(1 for t in ktok if _tok_in(t, qtok))"

JOBS = {
    "glossary_db.py": [
        ("def _key_hit(key, ql, qtok):\n", HELPER_KV),
        (HIT_OLD, HIT_NEW),
    ],
    "intentmap_db.py": [
        ("def _key_hit(key, ql, qtok):\n", HELPER_KV),
        (HIT_OLD, HIT_NEW),
    ],
    "disambig_db.py": [
        ("def match(conn, query, tanggal=None, limit=5):\n", DIS_HELPER),
        (
            '    ql = (query or "").lower()\n    if not ql.strip():\n        return []\n    rows = conn.execute(',
            '    ql = (query or "").lower()\n    if not ql.strip():\n        return []\n    qtok = set(_tokens(query))\n    rows = conn.execute(',
        ),
        (
            '        keys = [d["pemicu"].lower()] + [str(p).lower() for p in (d.get("pola") or [])]\n        if not any(k and k in ql for k in keys):\n            continue',
            '        keys = [d["pemicu"]] + [str(p) for p in (d.get("pola") or [])]\n        if not any(_key_hit(k, ql, qtok) for k in keys):\n            continue',
        ),
    ],
}


def patch_file(path, pairs):
    if not os.path.exists(path):
        print("  ! LEWati (tak ada):", path); return False
    txt = io.open(path, encoding="utf-8").read()
    if "_tok_in" in txt:
        print("  = sudah dipatch, lewati:", path); return False
    new = txt
    for old, rep in pairs:
        n = new.count(old)
        if n != 1:
            print("  !! GAGAL %s: anchor ditemukan %dx (harus 1):\n     %r" % (path, n, old[:60]))
            return False
        new = new.replace(old, rep, 1)
    io.open(path + ".bak", "w", encoding="utf-8").write(txt)
    io.open(path, "w", encoding="utf-8").write(new)
    py_compile.compile(path, doraise=True)
    print("  OK dipatch (+backup .bak, py_compile OK):", path)
    return True


def main():
    ok = 0
    for path, pairs in JOBS.items():
        print("->", path)
        if patch_file(path, pairs):
            ok += 1
    print("\nSelesai. File dipatch:", ok)


if __name__ == "__main__":
    main()
