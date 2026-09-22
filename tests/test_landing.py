from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LandingContractTest(unittest.TestCase):
    def test_assets_are_isolated_and_linked(self):
        html = (ROOT / "templates/landing/landing.html").read_text(encoding="utf-8")
        self.assertIn('/static/landing/landing.css', html)
        self.assertIn('/static/landing/landing.js', html)
        self.assertNotIn('base.css', html)
        self.assertNotIn('base.js', html)
        self.assertNotIn('{% extends', html)

    def test_public_and_authenticated_routes_are_separate(self):
        landing_routes = (ROOT / "routes/landing_routes.py").read_text(encoding="utf-8")
        pipeline_routes = (ROOT / "pipeline/routes.py").read_text(encoding="utf-8")
        app_core = (ROOT / "app_core.py").read_text(encoding="utf-8")
        self.assertIn('@app.get("/"', landing_routes)
        self.assertIn('app.add_api_route("/app"', pipeline_routes)
        self.assertIn('"/", "/login"', app_core)

    def test_frontend_files_stay_within_source_size_policy(self):
        paths = [
            ROOT / "templates/landing/landing.html",
            ROOT / "static/landing/landing.css",
            ROOT / "static/landing/landing.js",
        ]
        for path in paths:
            with self.subTest(path=path.name):
                self.assertLessEqual(len(path.read_text(encoding="utf-8").splitlines()), 400)

    def test_accessible_basics_exist(self):
        html = (ROOT / "templates/landing/landing.html").read_text(encoding="utf-8")
        css = (ROOT / "static/landing/landing.css").read_text(encoding="utf-8")
        self.assertIn('lang="id"', html)
        self.assertIn('href="#main"', html)
        self.assertIn('aria-label="Navigasi utama"', html)
        self.assertIn('prefers-reduced-motion', css)


if __name__ == "__main__":
    unittest.main()
