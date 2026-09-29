from pathlib import Path
import os
import tempfile
import unittest

import db.users_db as usr

ROOT = Path(__file__).resolve().parents[1]

class UsersSecurityTest(unittest.TestCase):
    def test_user_list_includes_override_counts(self):
        with tempfile.TemporaryDirectory() as folder:
            conn = usr.connect(os.path.join(folder, "users.db"))
            usr.init_db(conn)
            created = usr.create_user(conn, "person", "secret1", role="viewer")
            uid = created["user"]["id"]
            conn.execute("INSERT INTO user_area_grants(user_id,area,allow) VALUES(?,?,?)", (uid, "common", 1))
            conn.execute("CREATE TABLE IF NOT EXISTS user_menu_grants(user_id INTEGER,menu_key TEXT,allow INTEGER)")
            conn.execute("INSERT INTO user_menu_grants(user_id,menu_key,allow) VALUES(?,?,?)", (uid, "m_sosmed_monitor", 0))
            conn.commit()
            row = next(x for x in usr.list_users(conn) if x["id"] == uid)
            self.assertEqual(row["override_area_count"], 1)
            self.assertEqual(row["override_menu_count"], 1)
            conn.close()

    def test_self_protection_and_menu_cleanup_contract(self):
        routes = (ROOT / "routes" / "auth_routes.py").read_text(encoding="utf-8")
        db = (ROOT / "db" / "users_db.py").read_text(encoding="utf-8")
        access_js = (ROOT / "static" / "app" / "access.js").read_text(encoding="utf-8")
        self.assertIn("Tidak dapat menonaktifkan akun yang sedang digunakan", routes)
        self.assertIn("Tidak dapat menghapus akun yang sedang digunakan", routes)
        self.assertIn("DELETE FROM user_menu_grants", db)
        self.assertIn("new URLSearchParams(location.search).get('user_id')", access_js)

if __name__ == "__main__":
    unittest.main()
