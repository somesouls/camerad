#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OPSI B untuk STEP 5 (Qwen Judgement Top 5) - backend llm_fix_final_combined.py

Menambah LABEL OUTPUT BARU = 7:
  7 = intent relevan ADA, TAPI kalimat TIDAK LAYAK dijadikan satu training
      phrase (multi-masalah / kepanjangan-bertele-tele) -> berisiko menurunkan
      match rate intent lain bila di-seed.

Makna label lama tetap bersih:
  1-5 = kandidat terpilih | 0 = tak ada yang cocok | 6 = tidak mandiri.

Di sheet "Analisis Fallback", label 7 -> Catatan LLM = "TIDAK LAYAK TRAINING",
sehingga otomatis TIDAK ikut jadi seed (bukan TINDAK LANJUT) di Step 6/10/11.

Aman diulang (idempotent), buat .bak sekali, verifikasi py_compile.
Pakai:  python fix_step5_opsiB.py llm_fix_final_combined.py
"""
import sys, os, shutil, py_compile

PILIH7 = (
    "Pilih 7 jika pertanyaan SEBENARNYA memiliki intent relevan (salah satu\n"
    "peringkat memenuhi) TETAPI kalimatnya TIDAK LAYAK dijadikan satu training\n"
    "phrase Dialogflow, yaitu bila memenuhi SALAH SATU:\n"
    "- memuat LEBIH DARI SATU masalah/pertanyaan berbeda sekaligus; ATAU\n"
    "- sangat panjang & bertele-tele (banyak kalimat/klausa) sehingga bila\n"
    "  dilatih berisiko MENURUNKAN match rate intent lain.\n"
    "Gunakan 7 HANYA untuk soal kelayakan seed; bila kalimat sudah ringkas &\n"
    "fokus pada satu masalah, tetap pakai 1-5 seperti biasa."
)

EDITS = [
    # (label, old, new, idempotency_marker)
    ("parse_choice: set 0-7",
     'if value in {"0", "1", "2", "3", "4", "5", "6"}:',
     'if value in {"0", "1", "2", "3", "4", "5", "6", "7"}:',
     '"6", "7"}:'),

    ("parse_choice: regex 0-7",
     'match = re.search(r"(?<!\\d)([0-6])(?!\\d)", value)',
     'match = re.search(r"(?<!\\d)([0-7])(?!\\d)", value)',
     '([0-7])'),

    ("system_msg: 0 sampai 7",
     '"Output tepat satu digit 0 sampai 6."',
     '"Output tepat satu digit 0 sampai 7."',
     '0 sampai 7.'),

    ("konstanta CATATAN_TIDAK_LAYAK",
     'CATATAN_TIDAK_MANDIRI = "PERTANYAAN TIDAK MANDIRI"',
     'CATATAN_TIDAK_MANDIRI = "PERTANYAAN TIDAK MANDIRI"\n'
     'CATATAN_TIDAK_LAYAK = "TIDAK LAYAK TRAINING"',
     'CATATAN_TIDAK_LAYAK ='),

    ("prompt: blok Pilih 7",
     'kalimat tanpa objek/masalah/layanan yang jelas.\n\nLARANGAN:',
     'kalimat tanpa objek/masalah/layanan yang jelas.\n\n' + PILIH7 + '\n\nLARANGAN:',
     'Pilih 7 jika'),

    ("prompt: OUTPUT 0-7",
     'OUTPUT: keluarkan TEPAT satu angka (0, 1, 2, 3, 4, 5, atau 6). Tanpa penjelasan.',
     'OUTPUT: keluarkan TEPAT satu angka (0, 1, 2, 3, 4, 5, 6, atau 7). Tanpa penjelasan.',
     '6, atau 7)'),

    ("mapping: elif choice == 7",
     '            if choice == 6:\n'
     '                catatan = CATATAN_TIDAK_MANDIRI    # pertanyaan tidak mandiri\n'
     '            elif 1 <= choice <= 5:',
     '            if choice == 6:\n'
     '                catatan = CATATAN_TIDAK_MANDIRI    # pertanyaan tidak mandiri\n'
     '            elif choice == 7:\n'
     '                catatan = CATATAN_TIDAK_LAYAK      # relevan tapi tak layak di-seed\n'
     '            elif 1 <= choice <= 5:',
     'elif choice == 7:'),

    ("counter init",
     'count_tindak = count_manual = count_mandiri = 0',
     'count_tindak = count_manual = count_mandiri = count_tidak_layak = 0',
     'count_tidak_layak = 0'),

    ("counter klasifikasi",
     '            if catatan == CATATAN_TINDAK_LANJUT:\n'
     '                count_tindak += 1\n'
     '            elif catatan == CATATAN_TIDAK_MANDIRI:\n'
     '                count_mandiri += 1\n'
     '            else:\n'
     '                count_manual += 1',
     '            if catatan == CATATAN_TINDAK_LANJUT:\n'
     '                count_tindak += 1\n'
     '            elif catatan == CATATAN_TIDAK_MANDIRI:\n'
     '                count_mandiri += 1\n'
     '            elif catatan == CATATAN_TIDAK_LAYAK:\n'
     '                count_tidak_layak += 1\n'
     '            else:\n'
     '                count_manual += 1',
     'count_tidak_layak += 1'),

    ("cek konsistensi",
     '    if n_data_rows != (count_tindak + count_manual + count_mandiri):\n'
     '        print(f"[WARN] Inkonsistensi jumlah: records={n_data_rows} "\n'
     '              f"!= tindak+manual+mandiri="\n'
     '              f"{count_tindak + count_manual + count_mandiri}.", flush=True)',
     '    if n_data_rows != (count_tindak + count_manual + count_mandiri + count_tidak_layak):\n'
     '        print(f"[WARN] Inkonsistensi jumlah: records={n_data_rows} "\n'
     '              f"!= tindak+manual+mandiri+tidak_layak="\n'
     '              f"{count_tindak + count_manual + count_mandiri + count_tidak_layak}.", flush=True)',
     'mandiri+tidak_layak='),

    ("stats dict",
     '        "tidak_mandiri": count_mandiri,\n'
     '        "failed": len(failures),',
     '        "tidak_mandiri": count_mandiri,\n'
     '        "tidak_layak_training": count_tidak_layak,\n'
     '        "failed": len(failures),',
     '"tidak_layak_training":'),

    ("print ANALISIS",
     '        f"TIDAK_MANDIRI={count_mandiri} gagal={len(failures)} "',
     '        f"TIDAK_MANDIRI={count_mandiri} TIDAK_LAYAK={count_tidak_layak} "\n'
     '        f"gagal={len(failures)} "',
     'TIDAK_LAYAK={count_tidak_layak}'),
]


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Pakai: python fix_step5_opsiB.py llm_fix_final_combined.py")
    path = sys.argv[1]
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    bak = path + ".bak_opsib"
    if not os.path.exists(bak):
        shutil.copy2(path, bak)
        print("Backup dibuat: %s" % bak)
    for label, old, new, marker in EDITS:
        if marker in src:
            print("  [lewati] %s (sudah ada)" % label); continue
        if old not in src:
            raise SystemExit("  [GAGAL] anchor '%s' tidak ditemukan." % label)
        src = src.replace(old, new, 1)
        print("  [ok] %s" % label)
    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    py_compile.compile(path, doraise=True)
    print("BERES. %s ter-patch & lolos py_compile." % path)


if __name__ == "__main__":
    main()
