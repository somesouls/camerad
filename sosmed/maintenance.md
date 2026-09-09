# Panduan Pemeliharaan Modul Sosmed (camerad)

> Dokumen ini untuk **orang yang melanjutkan** proyek ini. Fokusnya: **apa yang
> rusak ketika Instagram / TikTok mengubah struktur web / HTML / API mereka**,
> **file mana yang harus diperiksa**, dan **cara mendiagnosisnya cepat**.
>
> Ringkasan mental model: modul ini **bukan** memakai API resmi IG/TikTok. Ia
> menjalankan **browser sungguhan (Playwright + Chromium/Chrome)** yang login
> sebagai akun kedua, membuka postingan akun resmi, lalu:
> 1. **Menyadap respons JSON** internal web IG/TikTok ("API sniffing"), dan
> 2. **Memanen dari DOM** untuk hal yang tidak lewat JSON (khusus IG: komentar
>    utama/root).
>
> Karena bergantung pada struktur internal pihak ketiga yang **bisa berubah
> sewaktu-waktu tanpa pemberitahuan**, bagian collector adalah yang **paling
> rapuh**. Backend (DB, pairing) dan halaman Q&A **relatif stabil**.

---

## 1. Peta file & tanggung jawab

| File | Tanggung jawab | Rapuh thd perubahan web? |
|---|---|---|
| `sosmed/ig_collector.py` | Ambil komentar Instagram (browser) | **SANGAT RAPUH** |
| `sosmed/tiktok_collector.py` | Ambil komentar TikTok (browser) | **SANGAT RAPUH** |
| `sosmed/x_collector.py` | Ambil mention/komentar X/Twitter | Rapuh (ikuti pola sama) |
| `sosmed/db.py` | Simpan + **pairing Q&A** (tanya↔jawab) | Stabil (logika internal) |
| `sosmed/routes.py` | Endpoint web + halaman Q&A/monitor/kelola | Stabil |
| `sosmed/monitor.py` | Pengelompokan tampilan halaman Monitor | Stabil |
| `sosmed/autopull.py` | Penjadwal tarik harian + `pull_log` (anti-dobel) | Stabil |
| `templates/sosmed_qna.html` | **Penyajian** halaman Q&A (modal utas) | Stabil |
| `templates/sosmed_monitor.html`, `sosmed_kelola.html` | Halaman monitor & kelola | Stabil |

**Aturan emas:** kalau gejalanya *"data tidak masuk / kosong / salah tarik"* →
masalah ada di **collector** (bagian 3–5). Kalau gejalanya *"data ada di DB tapi
tampil salah / bercampur di layar"* → masalah ada di **penyajian** (bagian 6).

---

## 2. Konsep data yang WAJIB dipahami dulu

Setiap komentar dinormalisasi jadi satu "item" dengan field kunci berikut
(lihat `_mk_ig_item` / `_mk_tt_item`). **Jangan ubah arti field ini** — seluruh
pairing & penyajian bergantung padanya:

- `platform`: `x` | `ig` | `tiktok`
- `external_id`: id unik komentar di platform (IG: `pk`, TikTok: `cid`)
- `conversation_id`: **penanda "satu postingan/utas"**. Nilainya BEDA per platform:
  - **X** = id utas percakapan (satu tanya-jawab). 
  - **IG** = **shortcode postingan** (mis. `Dc-kZ1DJHpH`). Jadi SEMUA komentar
    di satu postingan punya `conversation_id` sama.
  - **TikTok** = **`aweme_id`** (id video). Sama: semua komentar 1 video sama.
- `in_reply_to_id`: `external_id` komentar **induk** (kalau ini balasan). Root = kosong.
- `is_official`: `True` bila handle penulis ada di daftar akun resmi.
- `created_at`: ISO-8601 UTC.

> **Konsekuensi penting** (sumber banyak bug historis): karena di IG/TikTok
> `conversation_id` = seluruh postingan, endpoint `get_thread` akan mengembalikan
> **semua** komentar 1 postingan. Itu sebabnya halaman Q&A memakai `threadBranch()`
> untuk menyaring **satu cabang** saja saat ditampilkan (lihat bagian 6).

Akun resmi diatur via env `SOSMED_OFFICIAL_HANDLES` (default a.l. `kring_pajak`,
`kringpajak`, `pajakrepublik`, `ditjenpajakri`). **Catatan:** handle seperti
`kringpajak1500200` belum termasuk default — tambahkan bila perlu.

---

## 3. Instagram — `sosmed/ig_collector.py`

### Cara kerja singkat
1. Login (cookie `sessionid`) → buka profil target → kumpulkan shortcode postingan
   terbaru (`extract_media_codes`).
2. Buka tiap postingan `/p/<shortcode>/`. Fungsi `_load_comments` men-scroll
   bertahap + mengklik toggle **"Lihat komentar lain"** & **"Lihat balasan"**.
3. Komentar **balasan** datang lewat respons `/graphql` → disadap `_on_response`.
4. Komentar **utama/root** IG **tidak** lewat XHR (di-render server-side), jadi
   **dipanen dari DOM** lewat `_DOM_COMMENTS_JS`.

### Titik yang PALING sering rusak saat IG berubah (urut prioritas cek)

1. **Selektor CSS DOM untuk komentar root** — `_DOM_COMMENTS_JS`.
   - Paling rapuh: **`span._ap3a`** (kelas nama-teracak/hashed untuk username &
     teks). IG **rutin mengganti** kelas ini. Gejala: `dom_comments` = 0 padahal
     postingan jelas ada komentar.
   - Selektor lain di sana: `a[href*="/c/"]`, regex permalink
     `/(?:p|reel|reels|tv)/[A-Za-z0-9_-]+/c/(\d+)/`, `span[dir="auto"]`,
     `time[datetime]`, `a[role="link"][href^="/"]`.
   - **Cara perbaiki:** jalankan `SOSMED_IG_HEADLESS=0` (browser tampak), buka
     DevTools di komentar, cari kelas/atribut baru pembungkus username & teks,
     lalu update selektor di `_DOM_COMMENTS_JS`.

2. **Endpoint JSON yang disadap** — di `_on_response` dan `_MEDIA_RE`.
   - Saat ini mencocokkan URL berisi `/api/`, `/graphql`, `/comments/`,
     `/child_comments/`, dan `_MEDIA_RE = /media/(\d+)/(?:comments|child_comments)`.
   - Gejala jika berubah: `json_seen` naik tapi `comments_seen` = 0.
   - **Cara cek:** `SOSMED_IG_DEBUG=1` → lihat `trace` (URL + keys respons) di
     output untuk menemukan endpoint komentar baru, lalu sesuaikan filter URL.

3. **Bentuk/nama field JSON komentar** — `_is_ig_comment` & `_mk_ig_item`.
   - Field yang dipakai: `pk`/`id`/`comment_id`, `user.username`, `text`,
     `parent_comment_id`, `created_at`/`created_at_utc`,
     `comment_like_count`/`like_count`, `child_comment_count`.
   - Gejala: komentar tersadap tapi ter-skip (dianggap bukan komentar) atau field
     kosong. Perbaiki pemetaan sesuai kunci JSON baru.

4. **Teks/ikon tombol "muat lebih" & "lihat balasan"** — regex `_MORE_COMMENTS_SRC`,
   `_MORE_REPLIES_SRC`, dan ikon `_MORE_COMMENTS_ICON_JS` (`svg[aria-label]`).
   - Gejala: `more_comments_clicked`/`more_replies_clicked` = 0 → balasan tidak
     terbuka. IG kadang ganti wording (per-locale) atau ganti teks jadi ikon.
   - **Bantuan diagnosa:** dengan `SOSMED_IG_DEBUG=1`, output memuat
     `reply_candidates` = daftar wording tombol yang terlihat; tambahkan pola
     baru ke regex.

5. **Login / deteksi sesi** — `_is_logged_in`, `_has_auth_cookie` (cookie
   `sessionid`), form login `input[name='username']`, `input[name='password']`,
   tombol "Log in"/"Masuk". Jika IG ubah alur login, paling praktis pakai profil
   Chrome yang sudah login via `SOSMED_IG_USER_DATA_DIR`, atau sesi semi-manual
   `SOSMED_IG_HEADLESS=0 python -m sosmed.ig_collector login`.

### ENV penting IG
`SOSMED_IG_USER_DATA_DIR` (profil Chrome login), `SOSMED_IG_TARGET`,
`SOSMED_IG_MAX_POSTS` (0=semua), `SOSMED_IG_MAX_SCROLLS`, `SOSMED_IG_HEADLESS`,
`SOSMED_IG_DEBUG`. Shortcode IG boleh memuat `_`/`-` → **jangan** `split("_")`.

---

## 4. TikTok — `sosmed/tiktok_collector.py`

### Cara kerja singkat
1. Login (cookie `sessionid`/`sessionid_ss`; sering perlu **captcha slider** →
   diselesaikan manual, lihat `_check_and_wait_captcha`).
2. Buka profil target, scroll, panen link `/@<target>/video/` & `/@<target>/photo/`
   dari DOM (`a[href]`).
3. Buka tiap video (klik thumbnail / fallback `goto`), `_load_comments` mengklik
   **"View N replies"** dan scroll panel komentar.
4. Komentar (utama & balasan) **semua lewat JSON** `/api/comment/list/...` →
   disadap `_on_response`. (Berbeda dgn IG, TikTok tak perlu panen DOM.)

### Titik yang PALING sering rusak saat TikTok berubah (urut prioritas cek)

1. **Atribut `data-e2e`** — TikTok memakai atribut ini untuk elemen UI:
   - Buka balasan: `_click_replies` → `[data-e2e^="view-more-"]`.
   - Panel komentar: `[data-e2e="search-comment-container"]` /
     `div[class*="DivCommentListContainer"]` (di `_scroll_comment_section`).
   - Tutup overlay video: `[data-e2e="browse-close"]`.
   - Gejala jika berubah: balasan tak terbuka / panel tak ter-scroll / overlay
     tak tertutup sehingga video berikutnya gagal. **Cek nilai `data-e2e` baru
     via DevTools** dan update.

2. **Endpoint JSON** — `_on_response`: `/api/comment/list`, `/api/post/item_list`,
   `/api/user/detail`. Jika TikTok ganti path, `json`>0 tapi `comments`=0.
   Diagnosa dengan `SOSMED_TT_DEBUG=1`.

3. **Bentuk field JSON** — `_is_tt_comment` & `_mk_tt_item`: `cid`, `text`,
   `user.unique_id`/`uniqueId`, `aweme_id`, `reply_to_reply_id`/`reply_id`,
   `create_time`/`createTime`, `digg_count`, `reply_comment_total`. Sesuaikan
   bila kunci berubah.

4. **Pola URL postingan** — filter `/@<target>/video/` & `/@<target>/photo/` dan
   helper `_video_url`. Jika TikTok ubah format URL, update di sini.

5. **Captcha & anti-bot** — `.captcha-verify-container`/`#captcha-verify-container`.
   TikTok agresif soal bot. Default memakai **Chrome asli** (`SOSMED_TT_CHANNEL`
   default `chrome`) + opsi `playwright_stealth` (opsional). Bila makin ketat:
   jalankan `SOSMED_TT_HEADLESS=0` dan selesaikan captcha manual, atau pakai
   profil login `SOSMED_TT_USER_DATA_DIR`.

### ENV penting TikTok
`SOSMED_TT_USER_DATA_DIR`, `SOSMED_TT_TARGET`, `SOSMED_TT_MAX_VIDEOS` (0=semua),
`SOSMED_TT_MAX_SCROLLS`, `SOSMED_TT_HEADLESS`, `SOSMED_TT_CHANNEL`, `SOSMED_TT_DEBUG`.

---

## 5. X / Twitter — `sosmed/x_collector.py`
Collector pertama & pola acuan (login akun kedua, sniffing + normalisasi item).
Di X `conversation_id` sudah memisahkan satu utas tanya-jawab, sehingga halaman
Q&A menampilkannya apa adanya (tanpa `threadBranch`). Bila X (mis. perubahan
web) rusak, periksa dengan pola sama: endpoint JSON yang disadap, field komentar,
dan alur login/sesi.

---

## 6. Penyajian halaman Q&A — `templates/sosmed_qna.html`

Halaman Q&A memanggil `GET /api/sosmed/thread?platform=&conversation_id=`
(→ `routes.py:api_thread` → `db.py:get_thread`) yang mengembalikan **semua** baris
ber-`conversation_id` sama.

- **X**: tampil apa adanya (sudah 1 utas).
- **IG/TikTok**: karena `conversation_id` = seluruh postingan, JS `threadBranch()`
  menyaring hanya **satu cabang komentar** (komentar induk yang diklik + seluruh
  turunannya via `in_reply_to_id`). Ini **murni penyajian**; tidak mengubah data.

Kalau gejalanya "di layar tercampur komentar/jawaban tak terkait" → perbaiki di
sini, **bukan** di collector. Kalau perlu ubah pengelompokan, jangan sentuh
`db.py get_thread` (dipakai bersama); cukup ubah `threadBranch`/`openThread`.

---

## 7. Backend pairing — `sosmed/db.py` (JANGAN diubah tanpa perlu)
`ingest_items` → `_norm_item` → `_pair_conversation`:
- Semua komentar warga (root/balasan) → `item_type='pertanyaan'`.
- Komentar akun resmi → `balasan_resmi`; pertanyaan yang dijawab → status
  `terjawab` (dicari lewat penelusuran leluhur `in_reply_to_id`).
- Ada **smoke test** dengan angka pasti (mis. `n_new==9`) — jika menambah sampel
  uji, sesuaikan assert-nya. Jalankan: `python -m sosmed.db` (atau smoke
  collector: `python -m sosmed.ig_collector` / `python -m sosmed.tiktok_collector`).

---

## 8. Perintah diagnostik cepat

```bash
# Smoke test parsing (offline, tanpa browser) — pastikan logika ekstraksi sehat
python -m sosmed.ig_collector
python -m sosmed.tiktok_collector

# Cek status login/sesi
python -m sosmed.ig_collector diag
python -m sosmed.tiktok_collector diag

# Login semi-manual (browser tampak; selesaikan 2FA/captcha manual)
# PowerShell:
$env:SOSMED_IG_HEADLESS="0"; python -m sosmed.ig_collector login
$env:SOSMED_TT_HEADLESS="0"; python -m sosmed.tiktok_collector login

# Uji tarik 1 hari + lihat trace endpoint (diagnosa saat 0 hasil)
$env:SOSMED_IG_DEBUG="1"; python -m sosmed.ig_collector collect 2026-09-06 2026-09-06
$env:SOSMED_TT_DEBUG="1"; python -m sosmed.tiktok_collector collect 2026-09-06 2026-09-06

# Dump ke CSV untuk inspeksi manual (kolom utama/balasan berjenjang)
python -m sosmed.ig_collector dump 2026-09-06 2026-09-06 hasil_ig.csv
python -m sosmed.tiktok_collector dump --out hasil_tt.csv
```

### Cara membaca angka diagnostik (`info`)
- `json_seen` > 0 tapi `comments_seen` = 0 → **endpoint/field JSON berubah** (bagian 3.2/3.3, 4.2/4.3).
- IG `dom_comments` = 0 → **selektor DOM berubah** (bagian 3.1) — biasanya `span._ap3a`.
- `more_replies_clicked` = 0 → **wording/atribut tombol balasan berubah** (bagian 3.4 / 4.1).
- `logged_in` = false → **masalah sesi/login** (bagian 3.5 / 4.5).

---

## 9. Checklist "web IG/TikTok berubah, mulai dari mana?"

1. Jalankan smoke test offline dulu (`python -m sosmed.<platform>_collector`).
   Kalau ini gagal, logika parsing yang salah — bukan perubahan web.
2. Jalankan `collect` dengan `..._DEBUG=1` & `..._HEADLESS=0`. Amati:
   - Bisa login? (cookie sessionid) → kalau tidak, bagian login/sesi.
   - `json_seen` naik? → endpoint masih kena. Kalau `comments_seen` tetap 0,
     lihat `trace` untuk endpoint komentar baru.
   - (IG) `dom_comments` 0? → perbaiki `_DOM_COMMENTS_JS` (cek `span._ap3a`).
   - (TikTok) balasan tak terbuka? → cek atribut `data-e2e` di DevTools.
3. Perbaiki selektor/endpoint/field yang relevan, ulangi `collect` sampai angka
   sehat, lalu jalankan smoke test lagi sebelum commit.
4. Data lama di DB tidak berubah; koleksi harian anti-dobel via `pull_log`
   (`autopull.py`) berdasar `UNIQUE(platform, external_id)`.

---

## 10. Catatan penting lain
- **Jangan commit kredensial.** Semua rahasia lewat ENV (`SOSMED_*_USERNAME/PASSWORD`)
  atau profil Chrome (`SOSMED_*_USER_DATA_DIR`).
- Untuk stabilitas login jangka panjang, **pakai profil Chrome yang sudah login**
  jauh lebih andal daripada login otomatis by-password (rawan 2FA/captcha).
- Perubahan web pihak ketiga **normal terjadi**. Yang perlu dijaga: selektor DOM,
  filter URL endpoint, dan pemetaan field JSON — semuanya terkumpul di dua file
  collector agar mudah ditambal.
