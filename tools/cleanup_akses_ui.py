from pathlib import Path
p=Path(__file__).resolve().parents[1]/'templates'/'akses.html'
s=p.read_text(encoding='utf-8')
r=[
('Atur peran (jenjang &amp; menu yang boleh diakses) dan tambahan akses khusus per-pengguna. Menu beranda &amp; Profil selalu tersedia untuk semua peran. Perubahan langsung berlaku tanpa restart.','Atur identitas peran, kapabilitas, dan izin area/API dasar. Pengaturan sidebar per-tautan dilakukan di panel granular di bawah. Perubahan langsung berlaku tanpa restart.'),
('Menu yang bisa diakses (dikelompokkan per accordion sidebar)','Izin area/API dasar'),
('      <div id="rareas"></div>','      <p class="hint">Dipakai untuk membatasi endpoint dan aksi backend secara coarse. Ini bukan sumber kebenaran tautan sidebar; pengaturan tautan ada di panel granular.</p>\n      <div id="rareas"></div>'),
('Akses Khusus per-Pengguna','Override Area/API per Pengguna (opsional)'),
('Timpa akses menu untuk satu pengguna di atas perannya (mis. beri satu menu tambahan, atau cabut satu menu). Kosongkan ke “Ikuti peran” untuk mengembalikan ke bawaan peran.','Dipakai hanya untuk mengecualikan izin area/API dasar milik peran pada satu pengguna. Ini berbeda dari override menu sidebar granular di panel bawah.'),
('style="margin-top:26px;">\n    <h2>🔗 Akses per-Tautan Menu (granular)</h2>','style="margin-top:18px;">\n    <h2>🔗 Akses Sidebar per-Tautan Menu (utama)</h2>'),
('Kontrol tampil/tidaknya SETIAP tautan menu sidebar per peran, plus timpaan per pengguna. Jika sebuah peran belum diatur di sini, peran itu tetap mengikuti aturan “Menu” area di atas (perilaku lama, kompatibel mundur). Menu beranda Studio selalu tampil.','Ini adalah sumber kebenaran untuk visibilitas dan akses halaman sidebar. Peran menentukan default per-tautan; override pengguna dapat menampilkan atau menyembunyikan tautan tertentu. Jika belum dikonfigurasi, sistem tetap fallback ke izin area/API lama. Menu Studio selalu tampil.'),
('Timpaan per Pengguna','Override Menu Sidebar per Pengguna'),
('Untuk satu pengguna: “Ikuti peran”, “Tampilkan”, atau “Sembunyikan” tiap menu. Timpaan pengguna paling menentukan.','Untuk satu pengguna: “Ikuti peran”, “Tampilkan”, atau “Sembunyikan” tiap tautan. Override ini hanya untuk sidebar/halaman menu dan paling menentukan pada level menu.'),
('// Pengelompokan area ke accordion, mengikuti sidebar base.html.','// Pengelompokan izin area/API dasar; bukan daftar tautan sidebar.')]
for a,b in r:
 c=s.count(a)
 assert c==1,(c,a[:60])
 s=s.replace(a,b,1)
assert s.count('Akses Sidebar per-Tautan Menu (utama)')==1
assert s.count('Override Area/API per Pengguna (opsional)')==1
assert s.count('Override Menu Sidebar per Pengguna')==1
p.write_text(s,encoding='utf-8')
print('access UI labels clarified')
