from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LandingContractTest(unittest.TestCase):
    def test_assets_are_isolated_and_linked(self):
        html = (ROOT / "templates/landing/landing.html").read_text(encoding="utf-8")
        self.assertIn('/static/landing/landing.css?v=earth-clean-20260922', html)
        self.assertIn('/static/landing/landing.js', html)
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
            ROOT / "static/landing/landing-theme.js",
        ]
        for path in paths:
            with self.subTest(path=path.name):
                self.assertLessEqual(len(path.read_text(encoding="utf-8").splitlines()), 400)

    def test_accessible_theme_contract(self):
        html = (ROOT / "templates/landing/landing.html").read_text(encoding="utf-8")
        css = (ROOT / "static/landing/landing.css").read_text(encoding="utf-8")
        js = (ROOT / "static/landing/landing-theme.js").read_text(encoding="utf-8")
        self.assertIn('lang="id"', html)
        self.assertIn('href="#main"', html)
        self.assertIn('aria-label="Navigasi utama"', html)
        self.assertIn('lp-theme-toggle', html)
        for color in ('#23231a', '#8d4b2d', '#2d2c22', '#f7eee4'):
            self.assertIn(color, css)
        for color in ('#ffe8d1', '#d84a05', '#fff7ed', '#2d2018'):
            self.assertIn(color, css)
        self.assertIn('prefers-color-scheme: light', css)
        self.assertIn('prefers-reduced-motion', css)
        self.assertIn('localStorage', js)


if __name__ == "__main__":
    unittest.main()


class LandingStylesheetSourceTest(unittest.TestCase):
    def test_landing_has_one_stylesheet_source(self):
        html = (ROOT / "templates/landing/landing.html").read_text(encoding="utf-8")
        css = (ROOT / "static/landing/landing.css").read_text(encoding="utf-8")
        self.assertNotIn("landing-theme.css", html)
        self.assertIn("LANDING TOKENS: CANONICAL EARTH THEME", css)
        self.assertIn("#ffe8d1", css)
        self.assertIn("#d84a05", css)
        self.assertIn("#23231a", css)
        self.assertIn("#8d4b2d", css)


class LandingTypographyContractTest(unittest.TestCase):
    def test_balanced_type_scale_and_detail_colors(self):
        html = (ROOT / "templates/landing/landing.html").read_text(encoding="utf-8")
        css = (ROOT / "static/landing/landing.css").read_text(encoding="utf-8")
        self.assertIn("lp-type-balanced", html)
        self.assertIn("lp-display", html)
        self.assertIn("lp-heading", html)
        self.assertIn("lp-card-title", html)
        self.assertIn("LANDING TOKENS: CANONICAL EARTH THEME", css)
        self.assertIn("--lp-heading-text", css)
        self.assertIn("--lp-body-text", css)
        self.assertIn("--lp-accent-text", css)
        self.assertIn("clamp(2.75rem, 5.2vw, 4.5rem)", css)


class LandingCssCleanupContractTest(unittest.TestCase):
    def test_theme_tokens_are_canonical_and_fonts_are_readable(self):
        import re
        css = (ROOT / "static/landing/landing.css").read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"(?m)^:root\s*\{", css)), 1)
        self.assertEqual(len(re.findall(r'(?m)^:root\[data-theme="light"\]\s*\{', css)), 1)
        self.assertIn('--lp-font: Arial, "Helvetica Neue", Helvetica, sans-serif', css)
        self.assertIn('--lp-display-font: Arial, "Helvetica Neue", Helvetica, sans-serif', css)
        self.assertIn('--lp-label-font: Arial, "Helvetica Neue", Helvetica, sans-serif', css)
        self.assertIn('font-variant-ligatures: none', css)
        for legacy in ('#050510', '#7c5cff', '#00e5ff', '#ff3cac', '#58f6ad', '--lp-mono', 'Segoe UI Variable', 'Aptos'):
            self.assertNotIn(legacy, css)
