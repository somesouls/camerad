import io
import shutil
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image, ImageDraw, ImageFont
import fitz
from common import converter_text as text, converter_jobs as jobs


def image_bytes():
    image = Image.new('RGB', (900, 200), 'white')
    try:
        font = ImageFont.truetype('DejaVuSans.ttf', 48)
    except OSError:
        font = ImageFont.load_default(size=48)
    ImageDraw.Draw(image).text((30, 50), 'CAMERAD 2026', fill='black', font=font)
    out = io.BytesIO(); image.save(out, format='PNG')
    return out.getvalue()


class ConverterExtractionTest(unittest.TestCase):
    def test_digital_pdf_order_and_pages(self):
        doc = fitz.open()
        for sentence in ('Camerad digital document first page', 'Second page text preserved exactly'):
            page = doc.new_page(); page.insert_text((72, 72), sentence)
        data = doc.tobytes(); doc.close()
        with patch.object(text, '_ocr', side_effect=AssertionError('Digital PDF should not use OCR')):
            result = text.extract(data, 'document.pdf')
        self.assertEqual(result['pages'], 2)
        self.assertEqual(result['ocr_pages'], 0)
        self.assertIn('Camerad digital', result['text'])
        self.assertLess(result['text'].index('first page'), result['text'].index('Second page'))

    def test_scan_pdf_image_ocr_and_partial_failure(self):
        doc = fitz.open(); doc.new_page().insert_image(fitz.Rect(0, 0, 500, 150), stream=image_bytes())
        data = doc.tobytes(); doc.close()
        with patch.object(text, '_ocr', return_value='CAMERAD 2026'):
            result = text.extract(data, 'scan.pdf')
            self.assertEqual(result['ocr_pages'], 1)
            self.assertIn('CAMERAD 2026', result['text'])
            self.assertEqual(text.extract(image_bytes(), 'screen.png')['text'], 'CAMERAD 2026')
        with patch.object(text, '_ocr', side_effect=ValueError('OCR belum terpasang')):
            result = text.extract(data, 'scan.pdf')
            self.assertTrue(result['partial'])
            self.assertIn('OCR belum terpasang', result['warnings'][0])

    def test_validation_limits_password_and_cancel(self):
        for data, name in ((b'', 'x.pdf'), (b'bad', 'x.exe'), (b'bad', 'x.pdf'), (b'bad', 'x.png')):
            with self.assertRaises(Exception): text.extract(data, name)
        with patch.object(text, 'MAX_BYTES', 2), self.assertRaises(ValueError):
            text.extract(b'123', 'x.pdf')
        doc = fitz.open(); doc.new_page()
        data = doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw='owner', user_pw='secret'); doc.close()
        with self.assertRaisesRegex(ValueError, 'sandi'): text.extract(data, 'locked.pdf')
        doc = fitz.open()
        for _ in range(31): doc.new_page()
        data = doc.tobytes(); doc.close()
        with self.assertRaisesRegex(ValueError, '30 halaman'): text.extract(data, 'long.pdf')
        with self.assertRaisesRegex(ValueError, 'dibatalkan'): text.extract(image_bytes(), 'x.png', cancelled=lambda: True)
        self.assertEqual(text.safe_name('../../folder/x.png'), 'x.png')
        self.assertEqual(text.safe_name('C:\\folder\\x.png'), 'x.png')

    @unittest.skipUnless(shutil.which('tesseract'), 'Native OCR is tested by converter CI')
    def test_native_ocr(self):
        result = text.extract(image_bytes(), 'screen.png')
        self.assertIn('CAMERAD', result['text'].upper())
        self.assertIn('2026', result['text'])


class ConverterJobsTest(unittest.TestCase):
    def setUp(self):
        with jobs._LOCK: jobs._JOBS.clear()

    def test_owner_isolation_discard_and_async_result(self):
        with patch.object(jobs, 'extract', return_value={'text': 'hello'}):
            key = jobs.start('owner', image_bytes(), 'a.png')
            deadline = time.monotonic() + 3
            while jobs.status('owner', key)['state'] not in ('done', 'error') and time.monotonic() < deadline:
                time.sleep(.01)
        self.assertIsNone(jobs.status('other', key))
        self.assertFalse(jobs.discard('other', key))
        result = jobs.status('owner', key)
        self.assertEqual(result['state'], 'done')
        self.assertNotIn('owner', result)
        self.assertEqual(result['result']['text'], 'hello')
        self.assertTrue(jobs.discard('owner', key))
        self.assertIsNone(jobs.status('owner', key))

    def test_queue_and_retention_bounds(self):
        with jobs._LOCK:
            for index in range(jobs.MAX_ACTIVE):
                jobs._JOBS[str(index)] = {'owner': str(index), 'state':'queued', 'updated':time.monotonic()}
        with self.assertRaisesRegex(ValueError, 'Antrean'): jobs.start('new', b'123', 'a.pdf')
        with jobs._LOCK:
            jobs._JOBS.clear()
            jobs._JOBS['old'] = {'owner':'a', 'state':'done', 'updated':time.monotonic()-jobs.TTL-1}
        self.assertIsNone(jobs.status('a', 'old'))


class ConverterFrontendContractTest(unittest.TestCase):
    def test_accessible_assets_and_clipboard(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / 'templates/converter.html').read_text()
        js = (root / 'static/app/converter.js').read_text()
        css = (root / 'static/app/converter.css').read_text()
        for token in ('multiple', 'fileQueue', 'copyAll', 'downloadAll', 'role="status"', 'text-ocr-v1'):
            self.assertIn(token, html)
        for token in ('clipboardData', 'navigator.clipboard', 'text/plain;charset=utf-8', 'textContent', '/api/converter/start'):
            self.assertIn(token, js)
        self.assertNotIn('innerHTML', js)
        self.assertIn('prefers-reduced-motion', css)
        self.assertNotIn('--c-orange', css)


if __name__ == '__main__': unittest.main()
