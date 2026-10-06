"""Temporary surgical integration; removed after generated commit."""
from pathlib import Path
import hashlib


def patch(path, sha, old, new):
    p = Path(path)
    data = p.read_bytes()
    actual = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
    if actual != sha:
        raise SystemExit('Source changed unexpectedly: ' + path)
    source = data.decode('utf-8')
    if source.count(old) != 1:
        raise SystemExit('Expected one match: ' + path)
    p.write_text(source.replace(old, new, 1), encoding='utf-8')


patch('app_core.py', 'e4a49fbc0e4556bc6af798f25028e6938e3ab4e6',
      '    if path == "/profil" or path.startswith("/api/profil"):',
      '    if path == "/converter" or path.startswith("/api/converter/"):\n        return "chat"\n    if path == "/profil" or path.startswith("/api/profil"):')
with Path('app_core.py').open('a', encoding='utf-8') as out:
    out.write('\n\n# Local text converter; isolated from Studio/RAG.\ntry:\n    from routes import converter_routes as _converter_routes\n    _converter_routes.register(app, render_page, mc.menu_allowed)\nexcept Exception as _converter_exc:\n    print("[CONVERTER] registrasi route dilewati:", _converter_exc, flush=True)\n')

patch('db/menu_catalog.py', '4655f47c91e375be67a07c4353b96aaf7acff340',
      '    {"key": "m_laporan",',
      '    {"key": "m_converter", "group": "umum", "label": "Converter", "path": "/converter"},\n    {"key": "m_laporan",')
patch('templates/base.html', 'a492b849bf37f9bb972f61862965e3f27200cb1e',
      '        {% if menu is not defined or menu.m_laporan %}',
      '''        {% if menu is not defined or menu.m_converter %}<a class="tool-side{% if active_page == 'converter' %} active{% endif %}" href="/converter">
          <div class="ic c-green"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M8 13h8M8 17h5"/></svg></div>
          <b>Converter</b>
        </a>{% endif %}
        {% if menu is not defined or menu.m_laporan %}''')
patch('Dockerfile', '745d9de22e1490e033cba1bf04e24e7bd26b5960',
      '        curl ca-certificates',
      '        curl ca-certificates tesseract-ocr tesseract-ocr-ind tesseract-ocr-eng')
print('CONVERTER_INTEGRATED')
