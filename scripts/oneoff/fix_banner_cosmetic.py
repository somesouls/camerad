# -*- coding: utf-8 -*-
"""PR-19 (kosmetik): perbaiki banner startup web_app.py.

- Ganti placeholder <IP-PC-INI> dengan deteksi IP LAN nyata saat boot.
- Hapus baris "Backend internal ...:8000" yang menyesatkan: semua mesin RAG +
  Avaya AWE kini berjalan dalam SATU proses; llm_fix_final_combined.py TIDAK
  dijalankan otomatis oleh start.bat.

Aman & idempoten: menyunting HANYA bila blok lama ditemukan tepat 1x, dan
mempertahankan gaya newline berkas (CRLF/LF) apa adanya.

Pakai:  python scripts/oneoff/fix_banner_cosmetic.py [path/web_app.py]
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))  # scripts/oneoff/ -> root repo

OLD = (
    '    shown = "localhost" if host in ("0.0.0.0", "::") else host\n'
    '    print("=" * 64)\n'
    '    print(" Dialogflow + Avaya Pipeline (FastAPI) - FRONTEND / UI")\n'
    '    print(" BUKA DI BROWSER : http://%s:%d/" % (shown, port))\n'
    '    print(" (JANGAN buka http://0.0.0.0:%d - itu cuma alamat bind, bukan URL)" % port)\n'
    '    print(" Dari PC lain LAN: http://<IP-PC-INI>:%d/" % port)\n'
    '    print(" Backend internal (jangan dibuka manual): %s" % CONFIG["local_api_base"])\n'
    '    print("=" * 64)\n'
)

NEW = (
    '    shown = "localhost" if host in ("0.0.0.0", "::") else host\n'
    '\n'
    '    def _lan_ip():\n'
    '        """Deteksi IP LAN utama (tanpa benar-benar mengirim paket keluar)."""\n'
    '        import socket\n'
    '        s = None\n'
    '        try:\n'
    '            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n'
    '            s.connect(("8.8.8.8", 80))\n'
    '            return s.getsockname()[0]\n'
    '        except Exception:\n'
    '            try:\n'
    '                return socket.gethostbyname(socket.gethostname())\n'
    '            except Exception:\n'
    '                return "IP-PC-INI"\n'
    '        finally:\n'
    '            try:\n'
    '                if s is not None:\n'
    '                    s.close()\n'
    '            except Exception:\n'
    '                pass\n'
    '\n'
    '    lan_ip = _lan_ip()\n'
    '    print("=" * 64)\n'
    '    print(" Dialogflow + Avaya Pipeline (FastAPI) - FRONTEND / UI")\n'
    '    print(" BUKA DI BROWSER : http://%s:%d/" % (shown, port))\n'
    '    print(" (JANGAN buka http://0.0.0.0:%d - itu cuma alamat bind, bukan URL)" % port)\n'
    '    print(" Dari PC lain LAN: http://%s:%d/" % (lan_ip, port))\n'
    '    print(" (mesin RAG + Avaya AWE jalan di proses ini; tak perlu buka backend terpisah)")\n'
    '    print("=" * 64)\n'
)


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "web_app.py")
    if not os.path.isfile(target):
        print("[fix_banner] ABORT: tidak menemukan %s" % target)
        return 2
    with open(target, "rb") as f:
        raw = f.read()
    crlf = b"\r\n" in raw
    text = raw.decode("utf-8")
    if crlf:
        text = text.replace("\r\n", "\n")
    if NEW in text and OLD not in text:
        print("[fix_banner] sudah diterapkan sebelumnya; tidak ada perubahan.")
        return 0
    n = text.count(OLD)
    if n != 1:
        print("[fix_banner] ABORT: blok banner lama ditemukan %d kali (harus 1)." % n)
        return 2
    text = text.replace(OLD, NEW, 1)
    out = text.replace("\n", "\r\n") if crlf else text
    with open(target, "wb") as f:
        f.write(out.encode("utf-8"))
    print("[fix_banner] OK: banner web_app.py diperbarui (IP LAN nyata + hapus baris :8000).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
