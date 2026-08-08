#!/usr/bin/env python3
# Installer berurutan untuk paket perbaikan Camerad yang antre.
# Jalankan dari FOLDER PROYEK: python install.py
import os, sys, subprocess, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
CWD = os.getcwd()

def p(*a): print(*a, flush=True)

def find(name, *cands):
    for c in cands:
        if os.path.isfile(c):
            return c
    return None

def run(patcher, *args):
    script = os.path.join(HERE, patcher)
    if not os.path.isfile(script):
        p(f"  [MISS] patcher tidak ada: {patcher}"); return False
    for a in args:
        if not os.path.isfile(a):
            p(f"  [SKIP] target tidak ditemukan untuk {patcher}: {a}"); return True
    p(f"  -> python {patcher} {' '.join(args)}")
    r = subprocess.run([sys.executable, script, *args])
    if r.returncode != 0:
        p(f"  [ERROR] {patcher} keluar dengan kode {r.returncode} — hentikan."); return False
    return True

def main():
    pr = find('pipeline_routes.py', 'pipeline_routes.py')
    web = find('web_app.py', 'web_app.py')
    llm = find('llm_fix_final_combined.py', 'llm_fix_final_combined.py')
    base = find('base.html', os.path.join('templates','base.html'), 'base.html')
    tools = find('tools.html', os.path.join('templates','tools.html'), 'tools.html')
    tmpl_dir = 'templates' if os.path.isdir('templates') else '.'

    p('== 1) pipeline_routes.py: Step 9 LOAD -> Step 9 SAVE -> Step 10 laporan ==')
    if pr:
        if not run('fix_step9.py', pr): return 1
        if not run('fix_step9_save.py', pr): return 1
        if not run('fix_step10_report.py', pr): return 1  # step10_build_new.py di folder sama
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
    src_routes = os.path.join(HERE, 'audit_tp_routes.py')
    src_html = os.path.join(HERE, 'audit_tp.html')
    if os.path.isfile(src_routes):
        shutil.copy2(src_routes, os.path.join(CWD, 'audit_tp_routes.py'))
        p('  [ok] audit_tp_routes.py -> ' + CWD)
    if os.path.isfile(src_html):
        os.makedirs(tmpl_dir, exist_ok=True)
        shutil.copy2(src_html, os.path.join(tmpl_dir, 'audit_tp.html'))
        p('  [ok] audit_tp.html -> ' + tmpl_dir)

    p('\nSELESAI. Sekarang RESTART server + hard refresh (Ctrl+F5), lalu ikuti checklist di INSTALL.txt.')
    return 0

if __name__ == '__main__':
    sys.exit(main())
