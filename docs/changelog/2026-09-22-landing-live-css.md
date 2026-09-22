# Landing live CSS and contrast

- Landing HTML and assets are served without browser caching during the design-testing phase.
- Manual edits to `static/landing/landing.css` should appear after a normal refresh once the running server uses this commit.
- Dark and light theme edit points are documented at the top of the stylesheet.
- Buttons, cards, KPI panels, borders, shadows, and hover states use stronger contrast.
- Typography sizing rules no longer use `!important`, so manual overrides remain straightforward.
