# Public landing page

## Added

- A fully isolated public landing page at `/` with dedicated HTML, CSS, and JavaScript.
- Responsive, accessible, reduced-motion-aware presentation of Camerad capabilities.
- A dedicated authenticated application home at `/app`.
- Landing contract tests and CI execution for standard-library unit tests.

## Compatibility

- `/login` remains the authentication page.
- Successful login defaults to `/app`.
- Existing tools and APIs keep their routes and authorization behavior.
- The legacy application design system is not loaded by the public landing page.
