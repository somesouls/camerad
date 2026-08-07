#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Patch STEP 5 (Qwen Judgement Top 5) backend: llm_fix_final_combined.py

TUJUAN
------
Memisahkan keputusan "match intent" (LLM) dari keputusan "layak jadi training
phrase / seed" (aturan mekanis). Prompt Qwen 0-6 TIDAK diubah supaya makna label
tetap bersih. Kelayakan seed dihitung deterministik di kode dan disimpan pada
kolom terpisah di sheet "Analisis Fallback":
  - "Layak Training"  : YA / TIDAK
  - "Alasan Training" : alasan bila TIDAK layak (mis. kepanjangan / multi-kalimat)

Aman diulang (idempotent) & membuat .bak sekali. Verifikasi via py_compile.

Pakai:
    python fix_step5_trainable.py llm_fix_final_combined.py
"""
import sys, os, shutil, py_compile

HEADER_OLD = '    "Isi Intent",            # isi jawaban intent (dinamis dari dropdown)\n]'
HEADER_NEW = (
    '    "Isi Intent",            # isi jawaban intent (dinamis dari dropdown)\n'
    '    "Layak Training",        # deterministik: layak jadi seed training phrase?\n'
    '    "Alasan Training",       # alasan bila TIDAK layak dijadikan seed\n'
    ']'
)

IDX_OLD = 'IDX_ISI = HEADER_ANALISIS.index("Isi Intent") + 1'
IDX_NEW = (
    'IDX_ISI = HEADER_ANALISIS.index("Isi Intent") + 1\n'
    'IDX_LAYAK = HEADER_ANALISIS.index("Layak Training") + 1\n'
    'IDX_ALASAN_TRAIN = HEADER_ANALISIS.index("Alasan Training") + 1\n'
    '\n'
    '# Ambang kelayakan seed training phrase (deterministik, tanpa LLM).\n'
    'MAX_TRAINABLE_WORDS = int(os.environ.get("MAX_TRAINABLE_WORDS", "25"))\n'
    'MAX_TRAINABLE_SENTENCES = int(os.environ.get("MAX_TRAINABLE_SENTENCES", "2"))'
)

HELPER_ANCHOR = '    if len(q.split()) == 1 and len(q) <= 2:\n        return True, "Potongan sangat pendek tanpa konteks"\n    return False, ""'
HELPER_FUNC = HELPER_ANCHOR + '''

def is_trainable_question(question):
    """Deterministik: apakah frasa LAYAK dijadikan seed training phrase Dialogflow.
    Dipisah dari keputusan match LLM. Return (bool, alasan).
    Kalimat kepanjangan / multi-kalimat / multi-pertanyaan berisiko menurunkan
    match rate intent lain bila dilatih, jadi ditandai TIDAK layak.
    """
    text = normalize_text(question)
    if not text:
        return False, "Kosong"
    n_words = len(text.split())
    if n_words > MAX_TRAINABLE_WORDS:
        return False, "Terlalu panjang (%d kata > %d)" % (n_words, MAX_TRAINABLE_WORDS)
    n_sent = len([s for s in re.split(r"[.!?\\n]+", text) if s.strip()])
    if n_sent > MAX_TRAINABLE_SENTENCES:
        return False, "Multi-kalimat (%d kalimat)" % n_sent
    n_q = text.count("?")
    if n_q >= 2:
        return False, "Banyak pertanyaan sekaligus (%d tanda tanya)" % n_q
    return True, ""'''

WRITE_OLD = ('            ws.cell(out_row, 5, catatan)\n'
             '            ws.cell(out_row, IDX_INTENT, intent_awal)')
WRITE_NEW = (WRITE_OLD +
             '\n            _layak, _alasan_train = is_trainable_question(q)\n'
             '            ws.cell(out_row, IDX_LAYAK, "YA" if _layak else "TIDAK")\n'
             '            ws.cell(out_row, IDX_ALASAN_TRAIN, "" if _layak else _alasan_train)')


def apply(src, old, new, label, patched_marker):
    if patched_marker in src:
        print("  [lewati] %s sudah ter-patch." % label)
        return src
    if old not in src:
        raise SystemExit("  [GAGAL] anchor untuk '%s' tidak ditemukan. "
                         "File mungkin sudah berbeda versi." % label)
    print("  [ok] %s" % label)
    return src.replace(old, new, 1)


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Pakai: python fix_step5_trainable.py llm_fix_final_combined.py")
    path = sys.argv[1]
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()

    bak = path + ".bak"
    if not os.path.exists(bak):
        shutil.copy2(path, bak)
        print("Backup dibuat: %s" % bak)

    src = apply(src, HEADER_OLD, HEADER_NEW, "tambah 2 kolom header", '"Layak Training",')
    src = apply(src, IDX_OLD, IDX_NEW, "konstanta indeks + ambang", "IDX_LAYAK =")
    src = apply(src, HELPER_ANCHOR, HELPER_FUNC, "fungsi is_trainable_question", "def is_trainable_question(")
    src = apply(src, WRITE_OLD, WRITE_NEW, "tulis kolom Layak/Alasan Training", "is_trainable_question(q)")

    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    py_compile.compile(path, doraise=True)
    print("BERES. %s ter-patch & lolos py_compile." % path)


if __name__ == "__main__":
    main()
