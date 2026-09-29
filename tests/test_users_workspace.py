from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

class UsersWorkspaceContractTest(unittest.TestCase):
    def setUp(self):
        self.html = (ROOT / "templates" / "users.html").read_text(encoding="utf-8")
        self.css = (ROOT / "static" / "app" / "users.css").read_text(encoding="utf-8")
        self.js = (ROOT / "static" / "app" / "users.js").read_text(encoding="utf-8")

    def test_assets_are_external_and_versioned(self):
        self.assertIn("/static/app/users.css?v=green-pink-v1", self.html)
        self.assertIn("/static/app/users.js?v=green-pink-v1", self.html)
        self.assertNotIn("<style>", self.html)
        self.assertNotIn("<script>", self.html)

    def test_table_modal_filters_and_access_link(self):
        for item in ("userTable", "userDialog", "userSearch", "roleFilter", "statusFilter"):
            self.assertIn('id="%s"' % item, self.html)
        self.assertIn("/akses?user_id=${u.id}#overridesPanel", self.js)
        self.assertIn("override_menu_count", self.js)
        self.assertIn("override_area_count", self.js)

    def test_existing_user_endpoints_are_preserved(self):
        for endpoint in ("/api/users", "/api/users/save", "/api/users/delete", "/api/roles"):
            self.assertIn(endpoint, self.js)

    def test_self_actions_are_guarded_in_ui(self):
        self.assertIn("self=u.username===me.username", self.js)
        self.assertIn("Akun sendiri tidak dapat dinonaktifkan", self.js)
        self.assertIn("Akun sendiri tidak dapat dihapus", self.js)

    def test_green_pink_contract(self):
        self.assertNotIn("--c-orange", self.css)
        self.assertIn("var(--accent)", self.css)
        self.assertIn("prefers-reduced-motion", self.css)

if __name__ == "__main__":
    unittest.main()
