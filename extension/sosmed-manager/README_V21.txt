Social Manager Pro V21 - IG Parent-ID Thread Fix

Yang berubah:
1. Instagram tidak lagi menempelkan reply ke "thread terakhir".
2. Instagram mengambil ID komentar dari permalink /p/<shortcode>/c/<commentId>/.
3. Reply Instagram ditempel ke komentar utama hanya jika parentId hasil DOM cocok dengan commentId komentar utama.
4. Caption akun tetap dibuang.
5. TikTok tetap memakai logic V19/V20 yang sudah aman: comment-level-1, comment-level-2, dan endpoint comment/list/reply.

Catatan teknis:
- Untuk reply Instagram, parentId dicari dari komentar utama terakhir sebelum <ul> reply pada blok "Hide all replies".
- Console akan menampilkan table debug: mainId, jumlah replies, dan replyParentIds.
- Kalau replyParentIds tidak sama dengan mainId, berarti DOM post tersebut beda dan perlu inspect tambahan.
