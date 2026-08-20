# Update UI — Camerad Studio

## 1. Sidebar bersama di semua halaman
- Sidebar sekarang hidup di `base.html`, jadi SEMUA halaman (kecuali login) memuat
  sidebar yang persis sama seperti index.html: brand + tombol "Chat Baru" +
  daftar menu (Dashboard, Kelola Data, Glosarium, Disambiguasi, Peta Intent,
  Analisis Dialogflow) + daftar riwayat chat.
- Riwayat chat dibaca dari localStorage yang sama (`studio_chats`). Di halaman non-chat,
  klik item riwayat / "Chat Baru" akan membuka halaman chat (/) dengan sesi terpilih.
- Menu aktif ter-highlight otomatis sesuai halaman.

## 2. Sidebar hide default + hover + pin
- Default tersembunyi (off-canvas). Arahkan kursor ke tepi kiri layar -> sidebar muncul.
- Tombol pin (ikon panah) di pojok kanan-atas sidebar. Saat dipin, sidebar tetap
  tampil & konten bergeser; panah berputar sebagai indikator. Status pin disimpan
  di localStorage (`sidebarPinned`).
- Di mobile tetap pakai tombol menu (hamburger) + overlay seperti sebelumnya.

## 3. Rebrand "Camerad Studio"
- "Pipeline Studio" / "Pipeline Lokal DJP" -> "Camerad Studio" (sidebar, judul tab, login).
- Tagline: "Computer Automation for Monitoring, Evaluating, Research & Development".
- Label bot chat "Asisten Pipeline" -> "Asisten Camerad".

## 4. Logo & favicon dari /static
- Sidebar & login memakai `/static/logo-only.svg` (dibungkus kotak putih agar kontras).
- Favicon: `/static/favicon.svg` (dipasang di base.html & login.html).
- Logo lockup (`logo-lockup-stacked.svg`) tersedia bila ingin dipakai di tempat lain.

## 5. web_app.py
- Ditambahkan mount StaticFiles: `app.mount("/static", StaticFiles(directory=.../static))`.
  (Sebelumnya `/static` di-allow di middleware tapi TIDAK pernah di-serve -> 404.
  Sekarang file di folder static bisa diakses.)
- Judul FastAPI app -> "Camerad Studio".
