#!/usr/bin/env python3
# Installer berurutan untuk paket perbaikan Camerad yang antre (v2).
# Jalankan dari FOLDER PROYEK: python install.py
#
# Catatan v2: perbaikan Step 9 SAVE ("Data Edit Tidak Valid") sudah terpasang di
# mesin ini sebelumnya, jadi fix_step9.py / fix_step9_save.py DIBUANG dari alur.
# Yang tersisa untuk Step 9 hanyalah tweak step9_load opsional (non-fatal).
import os, sys, subprocess, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
CWD = os.getcwd()

def p(*a): print(*a, flush=True)

def find(name, *cands):
    for c in cands:
        if os.path.isfile(c):
            return c
    return None

def run(patcher, *args, optional=False):
    """Jalankan patcher. optional=True => kegagalan TIDAK menghentikan installer."""
    script = os.path.join(HERE, patcher)
    if not os.path.isfile(script):
        p(f"  [MISS] patcher tidak ada: {patcher}"); return optional
    for a in args:
        if not os.path.isfile(a):
            p(f"  [SKIP] target tidak ditemukan untuk {patcher}: {a}"); return True
    p(f"  -> python {patcher} {' '.join(args)}")
    r = subprocess.run([sys.executable, script, *args])
    if r.returncode != 0:
        if optional:
            p(f"  [WARN] {patcher} kode {r.returncode} (opsional) — dilewati, lanjut."); return True
        p(f"  [ERROR] {patcher} keluar dengan kode {r.returncode} — hentikan."); return False
    return True

def safe_copy(src, dst, label):
    """Salin file dengan aman. Lewati jika sumber == tujuan (mis. paket di-pull
    ke dalam folder proyek) dan JANGAN pernah menghentikan installer."""
    if not os.path.isfile(src):
        return
    try:
        if os.path.abspath(src) == os.path.abspath(dst) or (
            os.path.exists(dst) and os.path.samefile(src, dst)):
            p(f"  [lewati] {label} sudah di tempat (sumber = tujuan)."); return
    except OSError:
        pass
    try:
        shutil.copy2(src, dst)
        p(f"  [ok] {label} -> {dst}")
    except Exception as e:
        p(f"  [WARN] gagal menyalin {label} ({e}). Lewati — pastikan file sudah ada.")

def main():
    pr = find('pipeline_routes.py', 'pipeline_routes.py')
    web = find('web_app.py', 'web_app.py')
    llm = find('llm_fix_final_combined.py', 'llm_fix_final_combined.py')
    base = find('base.html', os.path.join('templates','base.html'), 'base.html')
    tools = find('tools.html', os.path.join('templates','tools.html'), 'tools.html')
    tmpl_dir = 'templates' if os.path.isdir('templates') else '.'

    p('== 1) pipeline_routes.py: Step 9 LOAD (opsional) -> Step 10 laporan (WAJIB) ==')
    if pr:
        run('fix_step9_load_only.py', pr, optional=True)   # tweak tampilan, non-fatal
        if not run('fix_step10_report.py', pr): return 1    # step10_build_new.py di folder sama
    else:
        p('  [SKIP] pipeline_routes.py tidak ditemukan')

    p('== 2) templates/tools.html: CSS scope -> Opsi B pill Step 6 ==')
    if tools:
        if not run('fix_tools_scope.py', tools): return 1
        if not run('fix_step6_pill_opsiB.py', tools): return 1
    else:
        p('  [SKIP] tools.html tidak ditemukan')

    p('== 3) base.html: modal solid -> wiring menu Audit ==')
    if base:
        if not run('fix_modal_canvas.py', base): return 1
        if web:
            if not run('fix_wire_audit_tp.py', web, base): return 1
        else:
            p('  [SKIP] web_app.py tidak ditemukan untuk wiring audit')
    else:
        p('  [SKIP] base.html tidak ditemukan')

    p('== 4) llm_fix_final_combined.py: Opsi B (label 7 TIDAK LAYAK TRAINING) ==')
    if llm:
        if not run('fix_step5_opsiB.py', llm): return 1
    else:
        p('  [SKIP] llm_fix_final_combined.py tidak ditemukan')

    p('== 5) Salin file menu Audit Training Phrase ==')
    safe_copy(os.path.join(HERE, 'audit_tp_routes.py'),
              os.path.join(CWD, 'audit_tp_routes.py'), 'audit_tp_routes.py')
    os.makedirs(tmpl_dir, exist_ok=True)
    safe_copy(os.path.join(HERE, 'audit_tp.html'),
              os.path.join(tmpl_dir, 'audit_tp.html'), 'audit_tp.html')

    p('\nSELESAI. Sekarang RESTART server + hard refresh (Ctrl+F5), lalu ikuti checklist di INSTALL.txt.')
    return 0

if __name__ == '__main__':
    sys.exit(main())
