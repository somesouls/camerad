# Landing live CSS and contrast

- Landing HTML is sent with `Cache-Control: no-store` during the design-testing phase.
- Every landing response gives the stylesheet a fresh timestamp query, so a normal page refresh requests the current `static/landing/landing.css` instead of reusing a cached copy.
- Manual edits apply after the running server has loaded this commit; no hard refresh is required.
- The stylesheet header identifies the dark-mode `:root` and light-mode `:root[data-theme="light"]` edit points.
- Dark primary is now `#f07840`; light primary is now `#b43a00`.
- Buttons, cards, KPI panels, borders, shadows, and hover states have stronger contrast.
- Cards have a visible four-pixel gradient accent line.
- Typography sizing rules no longer use `!important`, so manual overrides remain straightforward.
- Reduced-motion safety rules keep their accessibility-specific `!important` declarations.
