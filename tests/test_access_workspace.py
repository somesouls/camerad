from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class AccessWorkspaceContractTest(unittest.TestCase):
    def setUp(self):
        self.html = (ROOT / "templates" / "akses.html").read_text(encoding="utf-8")
        self.css = (ROOT / "static" / "app" / "access.css").read_text(encoding="utf-8")
        self.js = (ROOT / "static" / "app" / "access.js").read_text(encoding="utf-8")

    def test_access_workspace_has_three_clear_steps(self):
        for panel in ("rolesPanel", "roleAccessPanel", "overridesPanel"):
            self.assertIn('id="%s"' % panel, self.html)
        self.assertIn("Akses peran", self.html)
        self.assertIn("Pengecualian", self.html)

    def test_role_editor_is_a_dialog_and_table_is_independent(self):
        self.assertIn('<dialog class="role-dialog"', self.html)
        self.assertIn('class="table-scroll"', self.html)
        self.assertIn('id="roleSearch"', self.html)

    def test_access_assets_are_external_and_versioned(self):
        self.assertIn('/static/app/access.css?v=green-pink-v2', self.html)
        self.assertIn('/static/app/access.js?v=green-pink-v2', self.html)
        self.assertNotIn('<style>', self.html)
        self.assertNotIn('<script>', self.html)

    def test_permission_workflow_keeps_existing_endpoints(self):
        for endpoint in (
            "/api/roles", "/api/roles/save", "/api/roles/delete",
            "/api/menu-access/catalog", "/api/menu-access/role/save",
            "/api/menu-access/role/reset", "/api/menu-access/user/save",
            "/api/users/grants",
        ):
            self.assertIn(endpoint, self.js)

    def test_ui_has_search_summaries_and_explicit_confirmation(self):
        for element_id in ("mroleSearch", "muserSearch", "mroleSummary", "muserSummary"):
            self.assertIn('id="%s"' % element_id, self.html)
        self.assertIn("confirm(`Simpan", self.js)
        self.assertIn("prefers-reduced-motion", self.css)

    def test_old_orange_accent_is_not_used(self):
        self.assertNotIn("--c-orange", self.css)
        self.assertNotIn("#8d4b2d", self.css.lower())


if __name__ == "__main__":
    unittest.main()
