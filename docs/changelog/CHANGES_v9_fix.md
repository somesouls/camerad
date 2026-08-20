# Camerad Studio — v9 (Perbaikan Fase 2–4 + rework Tanya AI)

## Kenapa Fase 2–4 “tidak kelihatan” di server Anda
Setelah dicek menyeluruh, **kode & template Fase 2–4 sudah benar dan lengkap** di dalam paket:
- Semua halaman (`dashboard/data/glossary/disambig/intentmap/tools/deflection/lifecycle`) sudah `extends base.html`.
- Menu sidebar **Analisis Deflection** (Fase 2) dan **Siklus Hidup Intent** (Fase 3) ADA di `base.html` dan me-render di semua halaman.
- Route `/deflection` dan `/lifecycle` ADA di `web_app.py`.
- Panel **Tanya AI** (Fase 4) ADA.

Bukti: render tiap halaman anak lewat Jinja menampilkan `href="/deflection"`, `href="/lifecycle"`, dan kartu Tanya AI.

**Kesimpulan:** yang lama tidak berubah karena file baru **belum benar-benar terpasang/terjalankan** di server Anda — biasanya karena:
1. Unzip **tidak menimpa** file lama (tool unzip memilih “skip existing”), atau
2. Ter-ekstrak ke **subfolder** (mis. `camerad_studio/camerad_studio/...`), atau
3. Proses `uvicorn` lama masih jalan / restart tidak kena folder yang benar, atau
4. Cache template Jinja / cache browser.

### Penanda build agar mudah dicek
Ditambahkan label **“Build v9 · Fase 1–4”** di pojok kiri-bawah sidebar (dekat “Engine Aktif”). Jika label ini TIDAK muncul, berarti server masih menjalankan file lama — lihat langkah pemasangan di bawah.

## Langkah pemasangan yang benar
```bash
# 1) Hentikan server lama
pkill -f 'uvicorn web_app:app'   # atau Ctrl+C di terminalnya

# 2) Timpa BERSIH ke folder aplikasi (contoh folder: camerad_studio)
#    -o = overwrite tanpa tanya; -d = folder tujuan
unzip -o camerad_studio_v9.zip -d /path/ke/parent-folder
#    Pastikan hasilnya .../camerad_studio/web_app.py (BUKAN camerad_studio/camerad_studio/...)

# 3) Verifikasi timestamp file baru
ls -l camerad_studio/templates/base.html camerad_studio/web_app.py

# 4) Jalankan lagi
cd camerad_studio
uvicorn web_app:app --host 0.0.0.0 --port 8080
```
Buka aplikasi, **hard refresh** browser (Ctrl/Cmd+Shift+R). Pastikan label **Build v9** tampil di sidebar.

## Rework Fase 4 (sesuai permintaan)
Panel **Tanya AI** kini **meniru gaya panel Dashboard** dan diletakkan **inline di BAGIAN ATAS setiap halaman** (bukan lagi tombol mengambang):
- Kartu dengan judul “Tanya AI” + keterangan cakupan per-halaman, kotak input + tombol **Tanya AI**, chip contoh pertanyaan, dan kotak jawaban di bawahnya (sama pola dengan “AI Data Assistant” di Dashboard).
- Tampil otomatis di semua halaman **kecuali Dashboard** (Dashboard sudah punya panelnya sendiri) dan landing.
- Perilaku tetap sama seperti Fase 4: halaman pustaka (Glosarium/Disambiguasi/Peta Intent) dijawab **berpagar** hanya dari database internal; halaman data (Data/Deflection/Siklus Hidup/Analisis Dialogflow) memakai text-to-SQL read-only. Bila info tak ada, AI mengaku jujur.

## Ringkas fitur yang harus terlihat setelah v9 terpasang
- **Fase 1**: filter Intent Umum/Sistem di Dashboard. (sudah terlihat)
- **Fase 2**: menu **Analisis Deflection** → halaman deflection + manajemen kandidat.
- **Fase 3**: menu **Siklus Hidup Intent** → halaman lifecycle + retensi & soft-delete.
- **Fase 4**: kartu **Tanya AI** di atas tiap halaman (kecuali Dashboard).

## Validasi v9
- `py_compile web_app.py`: OK
- Parse Jinja 12 template: OK
- Render tiap halaman anak: link Deflection & Lifecycle + kartu Tanya AI + label Build v9 muncul: OK
- `node --check` JS kartu Tanya AI: OK
