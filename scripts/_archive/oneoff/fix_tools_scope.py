#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_tools_scope.py -- beri NAMESPACE pada CSS templates/tools.html.

MASALAH
-------
tools.html menaruh <style> GLOBAL lewat {% block head %}. Nama kelasnya bentrok
dengan shell base.html (mis. .dot, .brand, .logo) dan selektor elemen global
(input/select/textarea). Karena <style> tools dimuat SETELAH base.html, aturan
tools menimpa shell -> bulatan "Engine Aktif" di sidebar ikut membesar (.dot
52px vs 8px), dsb. Kebocoran ini dua arah.

PERBAIKAN
---------
1) Bungkus seluruh isi {% block content %} dalam <div id="dfp"> ... </div>.
2) Prefix SETIAP selektor di <style> tools.html dengan '#dfp '.
   - @keyframes / @font-face / @page disalin apa adanya (tidak di-prefix).
   - @media / @supports: isi di dalamnya ikut di-prefix.
   - Karena semua aturan naik +1 spesifisitas (id) secara seragam, urutan
     cascade INTERNAL tools tetap sama; hanya kebocoran ke/atau dari shell
     yang hilang.

Aman diulang (idempotent), membuat backup .bak_scope, dan memvalidasi hasil
(kurung seimbang + @keyframes utuh) sebelum menulis.

Pakai:
    python fix_tools_scope.py templates/tools.html
"""
import sys, os, shutil, re

SCOPE = "#dfp"
MARKER = "/* scoped:" + SCOPE + " */"


def split_top_commas(sel):
    """Pisah daftar selektor pada koma level-atas (hormati () dan [])."""
    parts = []
    buf = []
    depth_paren = 0
    depth_brack = 0
    for ch in sel:
        if ch == '(':
            depth_paren += 1
        elif ch == ')':
            depth_paren = max(0, depth_paren - 1)
        elif ch == '[':
            depth_brack += 1
        elif ch == ']':
            depth_brack = max(0, depth_brack - 1)
        if ch == ',' and depth_paren == 0 and depth_brack == 0:
            parts.append(''.join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append(''.join(buf))
    return parts


def prefix_prelude(prelude):
    """Prefix tiap selektor pada prelude dengan SCOPE."""
    # pisahkan whitespace pembuka agar rapi
    m = re.match(r"^(\s*)(.*)$", prelude, re.S)
    lead, body = m.group(1), m.group(2)
    if body.strip() == '':
        return prelude
    sels = split_top_commas(body)
    out = []
    for s in sels:
        st = s.strip()
        if st == '':
            continue
        out.append(SCOPE + ' ' + st)
    return lead + ', '.join(out)


def scope_css(css):
    i = 0
    n = len(css)
    out = []
    while i < n:
        c = css[i]
        if c in ' \t\r\n':
            out.append(c)
            i += 1
            continue
        if css[i:i+2] == '/*':
            j = css.find('*/', i + 2)
            if j == -1:
                out.append(css[i:])
                break
            out.append(css[i:j+2])
            i = j + 2
            continue
        if c == '@':
            k = i
            while k < n and css[k] not in '{;':
                k += 1
            header = css[i:k]
            low = header.lstrip().lower()
            if k < n and css[k] == ';':
                out.append(css[i:k+1])
                i = k + 1
                continue
            # blok at-rule: cari kurung penutup yang cocok
            depth = 0
            j = k
            while j < n:
                if css[j] == '{':
                    depth += 1
                elif css[j] == '}':
                    depth -= 1
                    if depth == 0:
                        j += 1
                        break
                j += 1
            if (low.startswith('@media') or low.startswith('@supports')):
                inner = css[k+1:j-1]
                out.append(css[i:k+1])          # header + '{'
                out.append(scope_css(inner))    # prefix isi
                out.append('}')
            else:
                # @keyframes / @font-face / @page / lainnya: salin verbatim
                out.append(css[i:j])
            i = j
            continue
        # selektor normal: baca prelude sampai '{'
        j = i
        while j < n and css[j] != '{':
            j += 1
        prelude = css[i:j]
        depth = 0
        b = j
        while b < n:
            if css[b] == '{':
                depth += 1
            elif css[b] == '}':
                depth -= 1
                if depth == 0:
                    b += 1
                    break
            b += 1
        block = css[j:b]
        out.append(prefix_prelude(prelude))
        out.append(block)
        i = b
    return ''.join(out)


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Pakai: python fix_tools_scope.py templates/tools.html")
    path = sys.argv[1]
    if not os.path.exists(path):
        raise SystemExit("[GAGAL] tidak ditemukan: %s" % path)
    with open(path, 'r', encoding='utf-8') as f:
        src = f.read()

    if MARKER in src:
        print("Sudah ter-scope (%s). Tidak ada perubahan." % SCOPE)
        return

    # --- 1) transform blok <style> ---
    head_re = re.compile(r"(\{%\s*block\s+head\s*%\}<style>)(.*?)(</style>\{%\s*endblock\s*%\})", re.S)
    m = head_re.search(src)
    if not m:
        raise SystemExit("[GAGAL] blok '{% block head %}<style>...</style>{% endblock %}' tidak ditemukan.")
    css = m.group(2)
    kf_before = len(re.findall(r"@keyframes", css))
    scoped = '\n' + MARKER + scope_css(css)
    # validasi kurung seimbang
    if scoped.count('{') != scoped.count('}'):
        raise SystemExit("[GAGAL] kurung {} tidak seimbang setelah transform; batal.")
    kf_after = len(re.findall(r"@keyframes", scoped))
    if kf_after != kf_before:
        raise SystemExit("[GAGAL] jumlah @keyframes berubah (%d->%d); batal." % (kf_before, kf_after))
    if (SCOPE + ' @keyframes') in scoped or (SCOPE + ' from') in scoped or (SCOPE + ' to') in scoped:
        raise SystemExit("[GAGAL] @keyframes ikut ter-prefix; batal.")
    new_src = src[:m.start()] + m.group(1) + scoped + m.group(3) + src[m.end():]

    # --- 2) bungkus konten dengan <div id="dfp"> ... </div> ---
    open_re = re.compile(r"(\{%\s*block\s+content\s*%\})")
    if not open_re.search(new_src):
        raise SystemExit("[GAGAL] '{% block content %}' tidak ditemukan.")
    new_src = open_re.sub(r'\1\n<div id="dfp">', new_src, count=1)

    close_re = re.compile(r"(\{%\s*endblock\s*%\}\s*\{%\s*block\s+scripts\s*%\})")
    if not close_re.search(new_src):
        raise SystemExit("[GAGAL] penutup '{% endblock %}{% block scripts %}' tidak ditemukan.")
    new_src = close_re.sub(r'</div>\n\1', new_src, count=1)

    bak = path + '.bak_scope'
    if not os.path.exists(bak):
        shutil.copy2(path, bak)
        print("Backup dibuat: %s" % bak)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(new_src)
    print("BERES. CSS tools.html kini ter-namespace di %s." % SCOPE)
    print("Restart server: bulatan 'Engine Aktif' normal lagi & tak ada lagi kebocoran gaya antar-menu.")


if __name__ == '__main__':
    main()
