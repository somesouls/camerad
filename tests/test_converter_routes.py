import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from routes import converter_routes


class ConverterRouteTest(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        @app.middleware('http')
        async def identity(request, call_next):
            request.state.user = {'id': request.headers.get('x-user', '1'), 'role': 'viewer'} if request.headers.get('x-user') != 'none' else None
            return await call_next(request)
        converter_routes.register(app, lambda *args: {'page':'converter'}, lambda role, key, user_id: user_id != 'denied')
        self.client = TestClient(app)

    def test_page_and_api_permissions(self):
        self.assertEqual(self.client.get('/converter').status_code, 200)
        self.assertEqual(self.client.get('/converter', headers={'x-user':'denied'}).status_code, 403)
        self.assertEqual(self.client.get('/api/converter/status/unknown', headers={'x-user':'none'}).status_code, 403)
        self.assertEqual(self.client.get('/api/converter/status/unknown').status_code, 404)

    def test_upload_and_owner_isolation(self):
        with patch.object(converter_routes.jobs, 'start', return_value='job') as start:
            response = self.client.post('/api/converter/start', files={'file':('x.png', b'123', 'image/png')})
            self.assertEqual(response.status_code, 202)
            self.assertEqual(start.call_args.args, ('1', b'123', 'x.png'))
        with patch.object(converter_routes.jobs, 'status', return_value=None) as status:
            self.assertEqual(self.client.get('/api/converter/status/job', headers={'x-user':'2'}).status_code, 404)
            status.assert_called_once_with('2', 'job')
        with patch.object(converter_routes.jobs, 'discard', return_value=False):
            self.assertEqual(self.client.post('/api/converter/discard/job').status_code, 404)

    def test_bad_multipart_missing_or_multiple_and_size_limit(self):
        self.assertEqual(self.client.post('/api/converter/start').status_code, 400)
        self.assertEqual(self.client.post('/api/converter/start', files=[('file', ('a.pdf', b'123')), ('file', ('b.pdf', b'123'))]).status_code, 400)
        with patch.object(converter_routes, 'MAX_BYTES', 1):
            response = self.client.post('/api/converter/start', files={'file':('x.png', b'x' * 70000)})
            self.assertEqual(response.status_code, 400)


if __name__ == '__main__': unittest.main()
