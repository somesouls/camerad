"""Ephemeral, owner-scoped converter jobs. Deploy one web worker (current topology)."""
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from common.converter_text import extract, safe_name, validate

TTL = 1800
MAX_JOBS = 40
MAX_ACTIVE = 8
MAX_USER_JOBS = 12
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix='converter')
_LOCK = threading.RLock()
_JOBS = {}


def _clean():
    now = time.monotonic()
    for key, job in list(_JOBS.items()):
        if job['state'] not in ('queued', 'running') and now - job['updated'] > TTL:
            del _JOBS[key]


def start(owner, data, filename):
    validate(data, safe_name(filename))
    with _LOCK:
        _clean()
        own = [j for j in _JOBS.values() if j['owner'] == owner]
        active = [j for j in _JOBS.values() if j['state'] in ('queued', 'running')]
        if len(active) >= MAX_ACTIVE or sum(j['owner'] == owner for j in active) >= 2:
            raise ValueError('Antrean OCR penuh. Tunggu proses sebelumnya selesai.')
        if len(own) >= MAX_USER_JOBS or len(_JOBS) >= MAX_JOBS:
            raise ValueError('Terlalu banyak hasil tersimpan sementara. Unduh hasil lalu bersihkan antrean.')
        key = secrets.token_urlsafe(24)
        job = {'owner': owner, 'state': 'queued', 'done': 0, 'total': 0,
               'filename': safe_name(filename), 'updated': time.monotonic(), 'cancel': False}
        _JOBS[key] = job
        try:
            _POOL.submit(_run, key, data)
        except Exception:
            del _JOBS[key]
            raise
        return key


def _run(key, data):
    def progress(done, total):
        with _LOCK:
            _JOBS[key].update(done=done, total=total, updated=time.monotonic())

    def cancelled():
        with _LOCK:
            return _JOBS[key]['cancel']

    with _LOCK:
        job = _JOBS[key]
        job['state'] = 'running'
    try:
        result = extract(data, job['filename'], progress, cancelled)
        with _LOCK:
            job.update(state='cancelled' if job['cancel'] else 'done', updated=time.monotonic())
            if not job['cancel']:
                job['result'] = result
    except Exception as exc:
        with _LOCK:
            job.update(state='cancelled' if job['cancel'] else 'error',
                       error=str(exc)[:500], updated=time.monotonic())
    finally:
        data = None


def status(owner, key):
    with _LOCK:
        _clean()
        job = _JOBS.get(key)
        if not job or job['owner'] != owner:
            return None
        return {k: v for k, v in job.items() if k not in ('owner', 'updated', 'cancel')}


def discard(owner, key):
    with _LOCK:
        job = _JOBS.get(key)
        if not job or job['owner'] != owner:
            return False
        if job['state'] in ('queued', 'running'):
            job['cancel'] = True
        else:
            del _JOBS[key]
        return True
