#!/usr/bin/env python3
from pathlib import Path

BASE = Path(__file__).resolve().parents[1] / "templates" / "akses.html"
text = BASE.read_text(encoding="utf-8")

replacements = [
    (
        '  <p class="hint">Atur peran (jenjang &amp; menu yang boleh diakses) dan tambahan akses khusus per-pengguna. Menu beranda &amp; Profil selalu tersedia untuk semua peran. Perubahan langsung berlaku tanpa restart.</p>',
        '  <p class="hint">Atur identitas peran, kapabilitas, dan izin area/API dasar. Pengaturan sidebar per-tautan dilakukan di panel granular di bawah. Perubahan langsung berlaku tanpa restart.</p>',
    ),
    (
        '      <label style="margin-top:12px;">Menu yang bisa diakses (dikelompokkan per accordion sidebar)</label>\n      <div id="rareas"></div>',
        '      <label style="margin-top:12px;">Izin area/API dasar</label>\n      <p class="hint">Dipakai untuk membatasi endpoint dan aksi backend secara coarse. Ini bukan sumber kebenaran tautan sidebar; pengaturan tautan ada di panel granular.</p>\n      <div id="rareas"></div>',
    ),
    (
        '      <h2>Akses Khusus per-Pengguna</h2>\n      <p class="hint">Timpa akses menu untuk satu pengguna di atas perannya (mis. beri satu menu tambahan, atau cabut satu menu). Kosongkan ke “Ikuti peran” untuk mengembalikan ke bawaan peran.</p>',
        '      <h2>Override Area/API per Pengguna (opsional)</h2>\n      <p class="hint">Dipakai hanya untuk mengecualikan izin area/API dasar milik peran pada satu pengguna. Ini berbeda dari override menu sidebar granular di panel bawah.</p>',
    ),
    (
        '  <div class="card" style="margin-top:26px;">\n    <h2>🔗 Akses per-Tautan Menu (granular)</h2>\n    <p class="hint">Kontrol tampil/tidaknya SETIAP tautan menu sidebar per peran, plus timpaan per pengguna. Jika sebuah peran belum diatur di sini, peran itu tetap mengikuti aturan “Menu” area di atas (perilaku lama, kompatibel mundur). Menu beranda Studio selalu tampil.</p>',
        '  <div class="card" style="margin-top:18px;">\n    <h2>🔗 Akses Sidebar per-Tautan Menu (utama)</h2>\n    <p class="hint">Ini adalah sumber kebenaran untuk visibilitas dan akses halaman sidebar. Peran menentukan default per-tautan; override pengguna dapat menampilkan atau menyembunyikan tautan tertentu. Jika belum dikonfigurasi, sistem tetap fallback ke izin area/API lama. Menu Studio selalu tampil.</p>',
    ),
    (
        '        <h2 style="font-size:14px;">Timpaan per Pengguna</h2>\n        <p class="hint">Untuk satu pengguna: “Ikuti peran”, “Tampilkan”, atau “Sembunyikan” tiap menu. Timpaan pengguna paling menentukan.</p>',
        '        <h2 style="font-size:14px;">Override Menu Sidebar per Pengguna</h2>\n        <p class="hint">Untuk satu pengguna: “Ikuti peran”, “Tampilkan”, atau “Sembunyikan” tiap tautan. Override ini hanya untuk sidebar/halaman menu dan paling menentukan pada level menu.</p>',
    ),
    (
        '// Pengelompokan area ke accordion, mengikuti sidebar base.html.',
        '// Pengelompokan izin area/API dasar; bukan daftar tautan sidebar.',
    ),
]

for old, new in replacements:
    count = text.count(old)
    assert count == 1, f"expected one occurrence, got {count}: {old[:70]}"
    text = text.replace(old, new, 1)

# Letak panel granular dijadikan panel utama sebelum editor peran lama.
marker = '  <!-- ====== Granularitas per-tautan menu (per-link RBAC) ====== -->'
start = text.index(marker)
end_marker = '\n  </div>\n</div>\n{% endblock %}'
end = text.index(end_marker, start) + len('\n  </div>')
granular = text[start:end]
text = text[:start] + text[end:]
split = text.index('  <div class="split">')
text = text[:split] + granular + '\n\n' + text[split:]

assert text.count('Akses Sidebar per-Tautan Menu (utama)') == 1
assert text.count('Izin area/API dasar') == 2
assert text.count('Override Area/API per Pengguna (opsional)') == 1
assert text.count('Override Menu Sidebar per Pengguna') == 1
assert text.index(marker) < text.index('  <div class="split">')
BASE.write_text(text, encoding="utf-8")
print('akses.html cleaned: area/API separated from granular sidebar menu controls')
