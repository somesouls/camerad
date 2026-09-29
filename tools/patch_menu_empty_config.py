from pathlib import Path

p = Path("db/menu_catalog.py")
s = p.read_text(encoding="utf-8")

def replace(old, new):
    global s
    if old not in s:
        raise SystemExit("Pola tidak ditemukan: %r" % old[:100])
    s = s.replace(old, new, 1)

replace(
    '''MENU_KEYS = [m["key"] for m in MENU_CATALOG]\n\n# Menu yang SELALU tampil''',
    '''MENU_KEYS = [m["key"] for m in MENU_CATALOG]\n# Penanda internal agar daftar kosong tetap berarti \"sudah dikonfigurasi\",\n# bukan jatuh kembali ke izin area/API lama.\n_CONFIG_MARKER = "__configured__"\n\n# Menu yang SELALU tampil''')
replace(
    '''        "configured": bool(rm),\n        "menus": sorted(rm) if rm else menus_for(role_key, None),\n''',
    '''        "configured": bool(rm),\n        "menus": sorted(k for k in rm if k in _BY_KEY) if rm else menus_for(role_key, None),\n''')
replace(
    '''        conn.execute("DELETE FROM role_menus WHERE role_key=?", (role_key,))\n        for k in keys:\n''',
    '''        conn.execute("DELETE FROM role_menus WHERE role_key=?", (role_key,))\n        conn.execute(\n            "INSERT OR IGNORE INTO role_menus (role_key,menu_key) VALUES (?,?)",\n            (role_key, _CONFIG_MARKER),\n        )\n        for k in keys:\n''')
p.write_text(s, encoding="utf-8")
print("MENU_EMPTY_CONFIG_PATCHED")
