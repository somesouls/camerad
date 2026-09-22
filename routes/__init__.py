# Paket routes: feature route FastAPI yang dulunya berkas datar di root.
#
# Landing didaftarkan saat paket routes pertama kali dimuat. Ini sengaja
# dilakukan di package boundary agar route publik tersedia sebelum route lama
# `/` dari pipeline didaftarkan, tanpa mengubah modul pipeline yang besar.
from app_core import _PUBLIC_PATHS, app
from routes import landing_routes

_PUBLIC_PATHS.update({"/", "/app"})
landing_routes.register(app)
