from pathlib import Path

def patch(path, old, new):
    p = Path(path); s = p.read_text(encoding="utf-8")
    if old not in s: raise SystemExit("Pola tidak ditemukan di %s: %r" % (path, old[:90]))
    p.write_text(s.replace(old, new, 1), encoding="utf-8")

patch("db/users_db.py", '''def list_users(conn):
    rows = conn.execute("SELECT * FROM users ORDER BY role, username").fetchall()
    return [_pub(r) for r in rows]
''', '''def list_users(conn):
    rows = conn.execute("SELECT * FROM users ORDER BY role, username").fetchall()
    out = []
    has_menu_grants = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='user_menu_grants'"
    ).fetchone() is not None
    for row in rows:
        item = _pub(row)
        area = conn.execute(
            "SELECT COUNT(*) c FROM user_area_grants WHERE user_id=?", (row["id"],)
        ).fetchone()
        item["override_area_count"] = int(area["c"] if area else 0)
        item["override_menu_count"] = 0
        if has_menu_grants:
            menu = conn.execute(
                "SELECT COUNT(*) c FROM user_menu_grants WHERE user_id=?", (row["id"],)
            ).fetchone()
            item["override_menu_count"] = int(menu["c"] if menu else 0)
        out.append(item)
    return out
''')
patch("db/users_db.py", '''    conn.execute("DELETE FROM user_area_grants WHERE user_id=?", (row["id"],))
    conn.execute("DELETE FROM users WHERE id=?", (row["id"],))
''', '''    conn.execute("DELETE FROM user_area_grants WHERE user_id=?", (row["id"],))
    try:
        conn.execute("DELETE FROM user_menu_grants WHERE user_id=?", (row["id"],))
    except Exception:
        pass
    conn.execute("DELETE FROM users WHERE id=?", (row["id"],))
''')
patch("routes/auth_routes.py", '''        uid = body.get("id")

        def _run():
''', '''        uid = body.get("id")
        me = getattr(request.state, "user", None) or {}
        if uid and int(uid) == int(me.get("id") or 0):
            if "aktif" in body and not bool(body.get("aktif")):
                return JSONResponse({"ok": False, "error": "Tidak dapat menonaktifkan akun yang sedang digunakan."})
            if body.get("role") and body.get("role") != me.get("role"):
                return JSONResponse({"ok": False, "error": "Tidak dapat mengubah peran akun yang sedang digunakan."})

        def _run():
''')
patch("routes/auth_routes.py", '''        if not uid:
            return JSONResponse({"ok": False, "error": "id kosong."})

        def _run():
''', '''        if not uid:
            return JSONResponse({"ok": False, "error": "id kosong."})
        me = getattr(request.state, "user", None) or {}
        if int(uid) == int(me.get("id") or 0):
            return JSONResponse({"ok": False, "error": "Tidak dapat menghapus akun yang sedang digunakan."})

        def _run():
''')
patch("static/app/access.js", '''const hash=location.hash.slice(1); activate($('#'+hash)?.classList.contains('workspace-panel')?hash:'rolesPanel'); }
''', '''const hash=location.hash.slice(1); activate($('#'+hash)?.classList.contains('workspace-panel')?hash:'rolesPanel'); const targetUser=new URLSearchParams(location.search).get('user_id'); if(targetUser&&users.some(u=>String(u.id)===targetUser)){ui.muser.value=targetUser;ui.guser.value=targetUser;await loadUserMenus();await loadGrants();} }
''')
print("USERS_WORKSPACE_PATCHED")
