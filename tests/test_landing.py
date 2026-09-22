from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LandingContractTest(unittest.TestCase):
    def test_assets_are_isolated_and_linked(self):
        html = (ROOT / "templates/landing/landing.html").read_text(encoding="utf-8")
        self.assertIn('/static/landing/landing.css', html)
        self.assertIn('/static/landing/landing.js', html)
        self.assertIn('/static/landing/landing-theme.css', html)
        self.assertIn('/static/landing/landing-theme.js', html)
        self.assertNotIn('base.css', html)
        self.assertNotIn('base.js', html)
        self.assertNotIn('{% extends', html)

    def test_public_and_authenticated_entries_are_separate(self):
        route = (ROOT / "routes/landing_routes.py").read_text(encoding="utf-8")
        package = (ROOT / "routes/__init__.py").read_text(encoding="utf-8")
        self.assertIn('@app.get("/"', route)
        self.assertIn('@app.get("/app")', route)
        self.assertIn('render_page(request, "index.html"', route)
        self.assertIn('usr.area_allowed(user.get("role"), "chat"', route)
        self.assertIn('_PUBLIC_PATHS.update({"/", "/app"})', package)

    def test_frontend_files_stay_within_source_size_policy(self):
        paths = [
            ROOT / "templates/landing/landing.html",
            ROOT / "static/landing/landing.css",
            ROOT / "static/landing/landing.js",
            ROOT / "static/landing/landing-theme.css",
            ROOT / "static/landing/landing-theme.js",
        ]
        for path in paths:
            with self.subTest(path=path.name):
                self.assertLessEqual(len(path.read_text(encoding="utf-8").splitlines()), 400)

    def test_accessible_theme_contract(self):
        html = (ROOT / "templates/landing/landing.html").read_text(encoding="utf-8")
        css = (ROOT / "static/landing/landing-theme.css").read_text(encoding="utf-8")
        js = (ROOT / "static/landing/landing-theme.js").read_text(encoding="utf-8")
        self.assertIn('lang="id"', html)
        self.assertIn('href="#main"', html)
        self.assertIn('aria-label="Navigasi utama"', html)
        self.assertIn('lp-theme-toggle', html)
        self.assertIn('#0b1020', css)
        self.assertIn('#f7567c', css)
        self.assertIn('#99e1d9', css)
        self.assertIn('#fcfcfc', css)
        self.assertIn('#fffae3', css)
        self.assertIn('#5d576b', css)
        self.assertIn('prefers-color-scheme: light', css)
        self.assertIn('prefers-reduced-motion', css)
        self.assertIn('localStorage', js)


if __name__ == "__main__":
    unittest.main()
