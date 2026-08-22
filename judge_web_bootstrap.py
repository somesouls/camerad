# -*- coding: utf-8 -*-
"""Bootstrap Judge Dialogflow (llm_fix_final_combined) di proses web_app.py.

Tujuan:
- Endpoint judge (Step 4/5/7/8/11: /api/judge-xlsx, /api/analyze-fallback,
  /api/mkta-analyze, /api/mkta-verdict, /api/update-usersays) tidak lagi butuh
  backend terpisah di :8000. Cukup ./start.bat -> web_app.py:8080 melayani
  semuanya dalam SATU proses.

Desain (mengikuti pola avaya/web_bootstrap.py): kecil, fail-soft, idempoten.
Diimpor dari app_core SETELAH avaya.web_bootstrap.register(app) agar patch
Avaya (speedpatch/dashpatch) sudah terpasang lebih dulu; keduanya idempoten
sehingga re-run saat llm_fix diimpor di sini aman (no-op).

Sebelum meng-import llm_fix, PIPELINE_NO_INSTALL dipaksa "1" supaya llm_fix TIDAK
menjalankan auto pip-install saat boot web (dependency berasal dari
requirements.txt / venv start.bat). Autentikasi endpoint judge memakai header
X-API-Key di dalam handler-nya sendiri; app_core menambahkan path-path ini ke
_PUBLIC_PATHS agar lolos middleware sesi.
"""
import os

_BOOTSTRAPPED = False

# Endpoint judge yang dipublikasikan ke app web utama: (path, nama fungsi handler).
JUDGE_ROUTES = [
    ("/api/judge-xlsx", "judge_xlsx"),
    ("/api/analyze-fallback", "analyze_fallback"),
    ("/api/mkta-analyze", "mkta_analyze"),
    ("/api/mkta-verdict", "mkta_verdict"),
    ("/api/update-usersays", "update_usersays"),
]

# Dipakai app_core._PUBLIC_PATHS (lolos middleware sesi; auth via X-API-Key).
JUDGE_PATHS = frozenset(path for path, _ in JUDGE_ROUTES)


def register(app):
    """Pasang endpoint judge ke FastAPI app web utama (satu proses)."""
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return app

    # Jangan pernah pip-install saat boot server web (memblokir boot). Venv
    # sudah disiapkan via requirements.txt + torch manual (lihat README).
    os.environ["PIPELINE_NO_INSTALL"] = "1"

    try:
        import llm_fix_final_combined as judge

        existing = {getattr(r, "path", None) for r in app.router.routes}
        added = []
        for path, fn_name in JUDGE_ROUTES:
            if path in existing:
                continue
            fn = getattr(judge, fn_name, None)
            if fn is None:
                continue
            app.add_api_route(path, fn, methods=["POST"])
            added.append(path)
        print("[JUDGE-WEB] endpoint judge aktif di web_app.py (satu proses): %s"
              % (", ".join(added) or "(tidak ada yang baru)"), flush=True)
        _BOOTSTRAPPED = True
    except Exception as exc:
        print("[JUDGE-WEB] endpoint judge tidak dimuat:", exc, flush=True)

    return app
