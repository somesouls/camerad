# Refactor Jinja2 base.html

## Ringkasan
Semua halaman kecuali `login.html` sekarang mewarisi `templates/base.html`.
`base.html` diturunkan langsung dari desain `index.html` (sidebar + topbar +
sistem tema gelap/terang), jadi semua halaman kini konsisten satu gaya.

## File yang diubah
- templates/base.html         (BARU) kerangka bersama + design system index + compat layer
- templates/index.html        -> {% extends "base.html" %} (chat stream/composer via block main)
- templates/dashboard.html     -> child; topbar/nav ganda dihapus (dipakai dari base)
- templates/data.html          -> child
- templates/disambig.html      -> child
- templates/glossary.html      -> child
- templates/intentmap.html     -> child
- templates/tools.html         -> child
- templates/users.html         -> child
- templates/login.html         TIDAK diubah (standalone, sesuai permintaan)
- web_app.py                   -> pakai Jinja2Templates + render_page(); route halaman diubah
- requirements.txt             -> tambah jinja2>=3.1.0

## Detail teknis
- Blok Jinja di base: title, head, side_newbtn, side_history, main, content, scripts.
- Variabel konteks: user_name, user_role, user_avatar, active_page (highlight menu aktif).
- Placeholder lama {{ CURRENT_USER_* }} diganti context var Jinja.
- Tema (dark default + toggle) & menu mobile dipindah ke script base (IIFE) agar tidak dobel.
- Compat CSS: nama variabel lama (--bg/--panel/--line/--text/--brand/--soft/--canvas/dst)
  dipetakan ke token index, sehingga halaman lama ikut tema tanpa ubah tiap komponen.
