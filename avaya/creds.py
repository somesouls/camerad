# -*- coding: utf-8 -*-
"""avaya/creds.py - Pembacaan kredensial Avaya dari .env yang tahan-banting.

Dipakai BERSAMA oleh AWE Chat & AWE Phone agar login memakai kredensial .env
berperilaku SAMA seperti login manual (mengetik langsung). Membuang artefak tak
terlihat yang kerap membuat server menolak ("kredensial tidak valid") padahal
nilainya "terlihat benar":
  - tanda kutip pembungkus:  AVAYA_PASSWORD="rahasia"  /  'rahasia'
  - spasi / tab / CR / LF di awal & akhir (umum saat menyunting .env di Windows)

CATATAN: HANYA membersihkan PEMBUNGKUS & UJUNG. Karakter sah di TENGAH nilai
(termasuk spasi atau tanda kutip di tengah) dipertahankan apa adanya, sehingga
kredensial yang memang mengandung karakter itu tidak berubah.
"""
import os


def sanitize(value):
    """Buang whitespace di kedua ujung + SATU lapis tanda kutip pembungkus serasi."""
    s = (value or "")
    # Hilangkan spasi/tab/CR/LF di kedua ujung (mis. sisa "\r\n" dari editor Windows).
    s = s.strip()
    # Buang SATU pasang tanda kutip pembungkus yang serasi (" atau ').
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("\"", "'"):
        s = s[1:-1].strip()
    return s


def username():
    """AVAYA_USERNAME bersih dari .env (string, bisa kosong)."""
    return sanitize(os.environ.get("AVAYA_USERNAME"))


def password():
    """AVAYA_PASSWORD bersih dari .env (string, bisa kosong)."""
    return sanitize(os.environ.get("AVAYA_PASSWORD"))


def base_url():
    """AVAYA_BASE_URL bersih dari .env (string, bisa kosong)."""
    return sanitize(os.environ.get("AVAYA_BASE_URL"))


def credentials():
    """(username, password, base_url) - semua sudah dibersihkan dari .env."""
    return username(), password(), base_url()


def is_configured():
    """True bila kredensial .env minimal (password) sudah diisi."""
    return bool(password())


def resolve(username_in="", password_in="", base_url_in=""):
    """Pakai kredensial MANUAL bila diisi; bila password kosong, JATUH ke .env
    (sudah dibersihkan). Kembalikan (username, password, base_url).

    Dipakai endpoint tarik/verifikasi manual supaya bila .env sudah diset,
    operator tidak perlu mengetik ulang password (form boleh dikosongkan).
    Input manual TIDAK diubah selain strip ujung username/base_url; password
    manual dipakai apa adanya (persis seperti perilaku login manual lama).
    """
    u = (username_in or "").strip()
    p = password_in or ""
    b = (base_url_in or "").strip()
    if not p:
        eu, ep, eb = credentials()
        if ep:
            u = u or eu
            p = ep
            b = b or eb
    return u, p, b
