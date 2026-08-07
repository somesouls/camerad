#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Wiring menu "Audit Training Phrase":
  1. web_app.py : import + audit_tp_routes.register(app)
  2. base.html  : link sidebar (di grup Dialogflow, setelah Analisis Dialogflow)

Aman diulang (idempotent), buat .bak sekali.
Pakai:
    python fix_wire_audit_tp.py web_app.py base.html
(atau templates/base.html sesuai lokasi)
"""
import sys, os, shutil

WEB_OLD = "import pustaka_routes\npustaka_routes.register(app)"
WEB_NEW = ("import pustaka_routes\npustaka_routes.register(app)\n"
           "import audit_tp_routes\naudit_tp_routes.register(app)")
WEB_MARK = "audit_tp_routes.register(app)"

NAV_OLD = (
    '        <a class="tool-side{% if active_page == \'tools\' %} active{% endif %}" href="/tools">\n'
    '          <div class="ic c-blue"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg></div>\n'
    '          <b>Analisis Dialogflow</b>\n'
    '        </a>'
)
NAV_NEW = NAV_OLD + (
    '\n        <a class="tool-side{% if active_page == \'audit_tp\' %} active{% endif %}" href="/audit-tp">\n'
    '          <div class="ic c-purple"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg></div>\n'
    '          <b>Audit Training Phrase</b>\n'
    '        </a>'
)
NAV_MARK = "active_page == 'audit_tp'"


def patch(path, old, new, mark, label):
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    if mark in src:
        print("  [lewati] %s (sudah ada)" % label); return
    if old not in src:
        raise SystemExit("  [GAGAL] anchor '%s' tidak ditemukan di %s." % (label, path))
    bak = path + ".bak_audit"
    if not os.path.exists(bak):
        shutil.copy2(path, bak); print("  backup: %s" % bak)
    src = src.replace(old, new, 1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    print("  [ok] %s" % label)


def main():
    if len(sys.argv) != 3:
        raise SystemExit("Pakai: python fix_wire_audit_tp.py web_app.py base.html")
    web, base = sys.argv[1], sys.argv[2]
    patch(web, WEB_OLD, WEB_NEW, WEB_MARK, "web_app.py register")
    patch(base, NAV_OLD, NAV_NEW, NAV_MARK, "base.html nav link")
    print("BERES.")


if __name__ == "__main__":
    main()
