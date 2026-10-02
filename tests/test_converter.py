# -*- coding: utf-8 -*-
"""tests/test_converter.py — Pengujian unit untuk utilitas converter (Excel, CSV, PDF)."""
import io
import unittest
import openpyxl
import pypdf

import converter.engine as eng


class ConverterEngineTest(unittest.TestCase):
    def setUp(self):
        # Buat Excel dummy 2 sheet
        wb = openpyxl.Workbook()
        ws1 = wb.active
        ws1.title = "Gaji"
        ws1.append(["Nama", "NIK", "Nominal"])
        ws1.append(["Budi", "012345678901234", 15000000])
        ws1.append(["Siti", "098765432109876", 12500000])

        ws2 = wb.create_sheet("Pajak")
        ws2.append(["Kode", "Tarif"])
        ws2.append(["PPH21", 0.05])

        xl_buf = io.BytesIO()
        wb.save(xl_buf)
        self.excel_bytes = xl_buf.getvalue()

        # Buat PDF dummy 3 halaman
        w = pypdf.PdfWriter()
        w.add_blank_page(width=200, height=200)
        w.add_blank_page(width=200, height=200)
        w.add_blank_page(width=200, height=200)
        pdf_buf = io.BytesIO()
        w.write(pdf_buf)
        self.pdf_bytes = pdf_buf.getvalue()

    def test_inspect_excel(self):
        info = eng.inspect_excel(self.excel_bytes)
        self.assertEqual(info["total_sheets"], 2)
        self.assertIn("Gaji", info["sheet_names"])
        self.assertIn("Pajak", info["sheet_names"])
        self.assertGreaterEqual(info["sheets"][0]["estimated_rows"], 3)

    def test_excel_to_csv_single(self):
        csv_bytes, fname, mime, meta = eng.excel_to_csv(
            self.excel_bytes, sheet_name="Gaji", delimiter=";", encoding="utf-8-sig"
        )
        self.assertTrue(fname.endswith(".csv"))
        text = csv_bytes.decode("utf-8-sig")
        lines = text.strip().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertIn("Budi;012345678901234;15000000", lines[1])

    def test_excel_to_csv_all_zip(self):
        zip_bytes, fname, mime, meta = eng.excel_to_csv(
            self.excel_bytes, sheet_name="__all__", delimiter=","
        )
        self.assertTrue(fname.endswith(".zip"))
        self.assertEqual(mime, "application/zip")
        self.assertEqual(meta["total_sheets"], 2)

    def test_inspect_and_convert_csv_to_excel(self):
        csv_data = "NPWP;Nama;Omzet\n012345678901234;PT Maju;500000000\n".encode("utf-8")
        info = eng.inspect_csv(csv_data)
        self.assertEqual(info["detected_delimiter"], ";")

        xl_bytes, fname, mime, meta = eng.csv_to_excel(
            csv_data, delimiter="auto", preserve_text=True
        )
        self.assertTrue(fname.endswith(".xlsx"))
        wb = openpyxl.load_workbook(io.BytesIO(xl_bytes))
        ws = wb.active
        # Pastikan leading zero tetap awet sebagai string
        npwp_val = ws["A2"].value
        self.assertEqual(npwp_val, "012345678901234")
        self.assertIsInstance(npwp_val, str)

    def test_pdf_inspect_and_merge(self):
        info = eng.inspect_pdf(self.pdf_bytes)
        self.assertEqual(info["page_count"], 3)
        self.assertFalse(info["is_encrypted"])

        merged_bytes, fname, mime, meta = eng.merge_pdfs([
            ("doc1.pdf", self.pdf_bytes),
            ("doc2.pdf", self.pdf_bytes),
        ])
        self.assertEqual(meta["total_files"], 2)
        self.assertEqual(meta["total_pages"], 6)

        r = pypdf.PdfReader(io.BytesIO(merged_bytes))
        self.assertEqual(len(r.pages), 6)

    def test_pdf_split_all(self):
        zip_bytes, fname, mime, meta = eng.split_pdf(self.pdf_bytes, mode="all")
        self.assertTrue(fname.endswith(".zip"))
        self.assertEqual(meta["files_created"], 3)

    def test_pdf_split_range_merged(self):
        pdf_out, fname, mime, meta = eng.split_pdf(
            self.pdf_bytes, mode="range", range_str="1, 3", merge_range_result=True
        )
        self.assertTrue(fname.endswith(".pdf"))
        self.assertEqual(meta["total_extracted"], 2)
        r = pypdf.PdfReader(io.BytesIO(pdf_out))
        self.assertEqual(len(r.pages), 2)

    def test_pdf_split_range_invalid(self):
        with self.assertRaises(ValueError):
            eng.split_pdf(self.pdf_bytes, mode="range", range_str="1-10")

    def test_pdf_split_step(self):
        zip_bytes, fname, mime, meta = eng.split_pdf(
            self.pdf_bytes, mode="step", step_n=2
        )
        self.assertTrue(fname.endswith(".zip"))
        self.assertEqual(meta["parts_created"], 2)  # hal 1-2, dan hal 3


if __name__ == "__main__":
    unittest.main()

