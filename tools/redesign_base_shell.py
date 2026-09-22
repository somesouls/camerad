from pathlib import Path
import re

root=Path(__file__).resolve().parents[1]
base=root/'templates/base.html'
js=root/'static/base.js'
text=base.read_text(encoding='utf-8')

def once(old,new):
 global text
 if text.count(old)!=1: raise SystemExit(f'anchor mismatch: {old[:70]!r} ({text.count(old)})')
 text=text.replace(old,new)

once('<link rel="stylesheet" href="/static/base.css">','<script src="/static/design-system/theme.js?v=green-pink-v2"></script>\n<link rel="stylesheet" href="/static/design-system/tokens.css?v=green-pink-v1">\n<link rel="stylesheet" href="/static/base.css?v=app-shell-v1">\n<link rel="stylesheet" href="/static/app/app-shell.css?v=green-pink-v1">')
once('<body>','<body class="app-shell">')
once('<div><b>Camerad Studio</b><span>Computer Automation for Monitoring, Evaluation, Research &amp; Development</span></div>','<div><b>Camerad Studio</b><span>Workspace internal · Analisis &amp; evaluasi</span></div>')
once('<span class="nb-label">Chat Baru</span>','<span class="nb-label">Percakapan baru</span>')
once('<div class="side-foot"><span class="dot"></span><span id="provChip">Engine Aktif</span><span style="margin-left:auto;font-size:10.5px;opacity:.7;">Build V9.1</span></div>','<div class="side-foot"><span class="dot"></span><span id="provChip">Sistem aktif</span><span class="build-label">Build V9.1</span></div>')
once('<button class="menu-btn" id="menuBtn">','<button class="menu-btn" id="menuBtn" type="button" aria-label="Buka navigasi">')
once('</button>\n        </div>\n        <div class="topbar-right">','</button>\n          <div class="workspace-label"><span>RUANG KERJA</span><b>{% block page_label %}Camerad Studio{% endblock %}</b></div>\n        </div>\n        <div class="topbar-right">')
text=text.replace(' style="text-decoration:none;"','').replace(' style="text-decoration: none;"','')
pattern=r'<button class="action-btn theme-toggle" id="theme-btn" aria-label="Toggle Mode">.*?</button>'
replacement='<button class="action-btn theme-toggle" id="theme-btn" data-theme-toggle type="button" aria-label="Ganti tema"><span data-theme-icon="star" aria-hidden="true">✦</span><span data-theme-icon="moon" aria-hidden="true" hidden>☾</span></button>'
text,count=re.subn(pattern,replacement,text,count=1,flags=re.S)
if count!=1: raise SystemExit('theme button anchor mismatch')
once('<script src="/static/base.js"></script>','<script src="/static/base.js?v=app-shell-v1"></script>\n<script src="/static/app/app-shell.js?v=green-pink-v1"></script>')
base.write_text(text,encoding='utf-8')

code=js.read_text(encoding='utf-8')
pattern=r"  var root = document\.documentElement;\n  var body = document\.body;\n  var saved = localStorage\.getItem\('theme'\) \|\| 'dark';\n  if \(saved === 'light'\) root\.setAttribute\('data-theme', 'light'\);\n  var tb = document\.getElementById\('theme-btn'\);\n  if \(tb\) tb\.addEventListener\('click', function\(\)\{\n    var c = root\.getAttribute\('data-theme'\) \|\| 'dark';\n    var n = \(c === 'dark'\) \? 'light' : 'dark';\n    root\.setAttribute\('data-theme', n\);\n    localStorage\.setItem\('theme', n\);\n  \}\);"
replacement='  var body = document.body;'
code,count=re.subn(pattern,replacement,code,count=1)
if count!=1: raise SystemExit('legacy theme runtime anchor mismatch')
js.write_text(code,encoding='utf-8')
print('base shell updated')
