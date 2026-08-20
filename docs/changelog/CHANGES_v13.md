# Camerad Studio - Perubahan v13

Pembaruan tampilan & logika (lanjutan dari v12).

## Dashboard
- Teks panjang pada "Kandidat Intent Baru", "Topik Paling Sering Dibahas", dan chip "Pencarian Intent Tepat" kini membungkus (wrap) sehingga tidak meluber horizontal.

## Analisis Deflection
- Kartu "Tanya AI" dipindah ke dalam kontainer konten (judul di atas, lebar sejajar konten) dan diperbaiki.
- Chip "Pecahan topik (intent bersih)" kini dapat dipilih (klik satu/lebih) untuk memfilter daftar percakapan pada sesi yang memuat intent tersebut.
- Status tindak lanjut (Skip / Tindak lanjut / Batalkan) kini ditujukan untuk INTENT terpilih, bukan per frasa. Backend menyimpan status di tabel baru intent_status; endpoint /api/deflection/intent-status/save.

## Kelola Data, Glosarium, Disambiguasi, Peta Intent
- Menu navigasi "Beranda / Dashboard / Tools" di header dihapus.
- Kartu "Tanya AI" dipindah ke dalam konten (judul di atas) dan diperbaiki.
- Kelola Data: ditambah menu "Impor data manual" (CSV/TSV/JSON) via endpoint /api/data/import untuk data lama.

## Siklus Hidup Intent & Analisis Dialogflow (Tools)
- Tanya AI diperbaiki; lebar konten dirapikan (max-width, tidak sempit).

## Studio Dokumen
- Lebar konten dirapikan (max-width).
- Hasil Studio kini dicatat sebagai riwayat chat (localStorage studio_chats) agar muncul di daftar percakapan.
- Dependensi parser dokumen sudah ada di requirements.txt sejak v11.

## Umum
- Logo tetap tampil saat sidebar diciutkan.
- Klik avatar di topbar mengarah ke /users.
- Penanda build: Build v13.
