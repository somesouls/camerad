"""Local, bounded PDF/image extraction; never sends documents to a cloud API."""
import io
import math
import os
import time
import warnings
from pathlib import PurePosixPath

MAX_BYTES = 20 * 1024 * 1024
MAX_PAGES = 30
MAX_PIXELS = 20_000_000
MAX_CHARS = 1_000_000
MAX_SECONDS = 240
EXTENSIONS = {'.pdf', '.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'}


def safe_name(name):
    name = PurePosixPath(str(name or 'dokumen').replace('\\', '/')).name
    return ''.join(c for c in name if ord(c) >= 32)[:180] or 'dokumen'


def validate(data, filename):
    if not data:
        raise ValueError('File kosong.')
    if len(data) > MAX_BYTES:
        raise ValueError('Maksimal 20 MB per file.')
    if PurePosixPath(filename.lower()).suffix not in EXTENSIONS:
        raise ValueError('Gunakan PDF, PNG, JPG, WEBP, BMP, atau TIFF.')


def _ocr(image):
    import pytesseract
    command = os.environ.get('CONVERTER_TESSERACT_CMD', '').strip()
    if command:
        pytesseract.pytesseract.tesseract_cmd = command
    try:
        available = set(pytesseract.get_languages(config=''))
        langs = '+'.join(lang for lang in ('ind', 'eng') if lang in available)
        if not langs:
            raise ValueError('Data bahasa OCR belum terpasang. Pasang Tesseract bahasa Indonesia/Inggris.')
        return pytesseract.image_to_string(image, lang=langs, config='--psm 3', timeout=20).strip()
    except pytesseract.TesseractNotFoundError as exc:
        raise ValueError('Mesin OCR belum terpasang. Pasang Tesseract dan atur CONVERTER_TESSERACT_CMD jika diperlukan.') from exc
    except RuntimeError as exc:
        raise ValueError('OCR melewati batas waktu. Coba gambar lebih kecil atau lebih jelas.') from exc


def _image(data):
    from PIL import Image, ImageOps
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in {'PNG', 'JPEG', 'WEBP', 'BMP', 'TIFF'}:
                raise ValueError('Format gambar tidak didukung.')
            if image.width * image.height > MAX_PIXELS:
                raise ValueError('Gambar terlalu besar; maksimal 20 megapiksel.')
            if getattr(image, 'n_frames', 1) > 1:
                raise ValueError('Gambar multiframe belum didukung. Unggah tiap halaman sebagai gambar atau PDF.')
            normalized = ImageOps.exif_transpose(image)
            if normalized.mode in ('RGBA', 'LA') or 'transparency' in normalized.info:
                rgba = normalized.convert('RGBA')
                normalized = Image.new('RGB', rgba.size, 'white')
                normalized.paste(rgba, mask=rgba.getchannel('A'))
            else:
                normalized = normalized.convert('RGB')
            return _ocr(normalized)


def extract(data, filename, progress=None, cancelled=None):
    filename = safe_name(filename)
    validate(data, filename)
    started = time.monotonic()
    progress = progress or (lambda done, total: None)
    cancelled = cancelled or (lambda: False)
    parts, notes, chars, ocr_pages = [], [], 0, 0

    def checkpoint():
        if cancelled():
            raise ValueError('Proses dibatalkan.')
        if time.monotonic() - started > MAX_SECONDS:
            raise ValueError('Batas waktu file terlampaui. Pecah PDF menjadi bagian lebih kecil.')

    def append(text):
        nonlocal chars
        chars += len(text)
        if chars > MAX_CHARS:
            raise ValueError('Hasil terlalu besar; maksimal 1 juta karakter. Pecah file terlebih dahulu.')
        parts.append(text)

    if filename.lower().endswith('.pdf'):
        import fitz
        if b'%PDF-' not in data[:1024]:
            raise ValueError('Isi file bukan PDF yang valid.')
        with fitz.open(stream=data, filetype='pdf') as document:
            if document.needs_pass:
                raise ValueError('PDF dilindungi sandi. Buka proteksinya sebelum mengunggah.')
            total = len(document)
            if not total or total > MAX_PAGES:
                raise ValueError('PDF harus berisi 1–30 halaman. Pecah PDF yang lebih panjang.')
            progress(0, total)
            for index, page in enumerate(document):
                checkpoint()
                text = page.get_text('text', sort=True).strip()
                if len(''.join(text.split())) < 12:
                    scale = min(2.5, math.sqrt(MAX_PIXELS / max(1, page.rect.width * page.rect.height)))
                    from PIL import Image
                    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csRGB, alpha=False)
                    if pix.width * pix.height > MAX_PIXELS:
                        raise ValueError('Halaman terlalu besar untuk OCR.')
                    try:
                        text = _ocr(Image.frombytes('RGB', (pix.width, pix.height), pix.samples))
                        ocr_pages += 1
                    except ValueError as exc:
                        notes.append('Halaman %s: %s' % (index + 1, exc))
                        text = ''
                append('--- Halaman %s ---\n%s' % (index + 1, text or '[Tidak ada teks yang terbaca]'))
                progress(index + 1, total)
    else:
        total = 1
        checkpoint()
        progress(0, total)
        text = _image(data)
        append(text)
        ocr_pages = 1
        progress(1, total)
        if not text:
            notes.append('Tidak ada teks yang terbaca. Coba screenshot lebih tajam atau lurus.')
    checkpoint()
    if notes:
        notes.append('Periksa hasil OCR; ejaan dan urutan tabel/kolom dapat berbeda dari dokumen asli.')
    return {'text': '\n\n'.join(parts), 'pages': total, 'ocr_pages': ocr_pages, 'warnings': notes,
            'filename': filename, 'partial': bool(notes), 'method': 'OCR' if ocr_pages == total else ('Teks + OCR' if ocr_pages else 'Lapisan teks PDF')}
