"""Converter HTTP layer: bounded uploads, menu permission and owner isolation."""
import os
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile
from starlette.concurrency import run_in_threadpool
from common import converter_jobs as jobs
from common.converter_text import MAX_BYTES


def register(app, render_page, menu_allowed):
    def owner(request):
        user = getattr(request.state, 'user', None) or {}
        if not user.get('id'):
            return None
        if not menu_allowed(user.get('role'), 'm_converter', user_id=user['id']):
            return None
        return str(user['id'])

    def denied():
        return JSONResponse({'ok': False, 'error': 'Akses converter ditolak atau sesi berakhir.'}, status_code=403)

    @app.get('/converter')
    async def converter_page(request: Request):
        if not owner(request):
            return denied()
        return render_page(request, 'converter.html', 'converter')

    @app.post('/api/converter/start')
    async def converter_start(request: Request):
        uid = owner(request)
        if not uid:
            return denied()
        if os.environ.get('CONVERTER_ENABLED', '1') == '0':
            return JSONResponse({'ok': False, 'error': 'Converter sedang dinonaktifkan.'}, status_code=503)
        received = 0

        async def limited_receive():
            nonlocal received
            message = await request.receive()
            received += len(message.get('body', b''))
            if received > MAX_BYTES + 65536:
                raise ValueError('Maksimal 20 MB per file.')
            return message

        bounded = Request(request.scope, receive=limited_receive)
        try:
            async with bounded.form(max_files=1, max_fields=0) as form:
                upload = form.get('file')
                if not isinstance(upload, UploadFile):
                    raise ValueError('Pilih satu file per permintaan; beberapa file diproses melalui antrean.')
                data = await upload.read(MAX_BYTES + 1)
                key = await run_in_threadpool(jobs.start, uid, data, upload.filename)
            return JSONResponse({'ok': True, 'job_id': key}, status_code=202)
        except ValueError as exc:
            return JSONResponse({'ok': False, 'error': str(exc)}, status_code=400)
        except Exception:
            return JSONResponse({'ok': False, 'error': 'Unggahan tidak valid. Coba lagi dengan PDF atau gambar.'}, status_code=400)

    @app.get('/api/converter/status/{key}')
    async def converter_status(key: str, request: Request):
        uid = owner(request)
        if not uid:
            return denied()
        result = jobs.status(uid, key)
        if result is None:
            return JSONResponse({'ok': False, 'error': 'Hasil tidak ditemukan/expired. Unggah ulang.'}, status_code=404)
        return JSONResponse({'ok': True, 'job': result}, headers={'Cache-Control': 'no-store'})

    @app.post('/api/converter/discard/{key}')
    async def converter_discard(key: str, request: Request):
        uid = owner(request)
        if not uid:
            return denied()
        if not jobs.discard(uid, key):
            return JSONResponse({'ok': False, 'error': 'Hasil tidak ditemukan.'}, status_code=404)
        return JSONResponse({'ok': True})
