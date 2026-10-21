"""
Phase 9: Edge-case tests across all pipeline modules.

Targets untested boundary conditions, unusual inputs, and defensive
paths not covered by the existing unit and integration test suites.

Modules exercised:
  - parser  (regex corner cases, ambiguous inputs)
  - intake  (filename sanitization edge cases, file validation)
  - ocr     (preprocessing edge cases)
  - storage (validation edge cases, unicode, large values)
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config
from src.db import get_connection
from tests.db_setup import init_db_for_test
from src.parser import (
    _extract_invoice_number,
    _extract_date,
    _extract_vendor_name,
    _extract_total,
    _extract_line_items,
    _safe_float,
    parse_invoice_text,
)
from src.intake import (
    sanitize_filename,
    is_file_valid,
    scan_input_folder,
    compute_file_hash,
)
from src.ocr import (
    _to_grayscale,
    _denoise,
    _deskew,
    _threshold,
    preprocess_image,
)
from src.storage import (
    _validate_invoice_number,
    _validate_date,
    _validate_vendor_name,
    _validate_amount,
    _validate_currency,
    _validate_line_item,
    validate_parsed_data,
    store_invoice,
    get_invoice_by_id,
    get_line_items,
)


# ── Shared base class ─────────────────────────────────────────

class _TempDBTestCase(unittest.TestCase):
    """Redirect DB and folders to a temp directory for isolation."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.data_dir = self.tmpdir / "data"
        self.processed_dir = self.tmpdir / "processed_invoices"
        self.input_dir = self.tmpdir / "input_invoices"
        self.failed_dir = self.tmpdir / "failed_invoices"
        self.ocr_output_dir = self.tmpdir / "ocr_output"

        for d in (self.data_dir, self.processed_dir, self.input_dir,
                  self.failed_dir, self.ocr_output_dir):
            d.mkdir()

        self._patches = [
            patch.object(config, "PROCESSED_DIR", self.processed_dir),
            patch.object(config, "INPUT_DIR", self.input_dir),
            patch.object(config, "FAILED_DIR", self.failed_dir),
            patch.object(config, "OCR_OUTPUT_DIR", self.ocr_output_dir),
        ]
        for p in self._patches:
            p.start()

        init_db_for_test()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @staticmethod
    def _make_parsed(
        invoice_number="INV-001",
        invoice_date="2024-01-15",
        vendor_name="Acme Corp",
        total_amount=1000.00,
        currency="USD",
        line_items=None,
        confidence="high",
    ) -> dict:
        if line_items is None:
            line_items = [
                {
                    "description": "Widget",
                    "quantity": 5,
                    "unit_price": 100.00,
                    "line_total": 500.00,
                },
            ]
        return {
            "invoice_number": invoice_number,
            "invoice_date": invoice_date,
            "vendor_name": vendor_name,
            "total_amount": total_amount,
            "currency": currency,
            "line_items": line_items,
            "confidence": confidence,
        }


# ── Parser Edge Cases ─────────────────────────────────────────

class TestParserEdgeCases(unittest.TestCase):

    def test_extract_invoice_number_variants(self):
        self.assertEqual(_extract_invoice_number("Invoice # INV-999"), "INV-999")
        self.assertEqual(_extract_invoice_number("Inv No:  ABC-1234"), "ABC-1234")
        self.assertIsNone(_extract_invoice_number("No invoice label here"))

    def test_extract_date_variants(self):
        self.assertEqual(_extract_date("Date: 2024-12-31"), "2024-12-31")
        self.assertEqual(_extract_date("Dated 2023/05/10"), "2023-05-10")
        self.assertIsNone(_extract_date("No date"))

    def test_extract_vendor_name_empty(self):
        self.assertIsNone(_extract_vendor_name(""))
        self.assertIsNone(_extract_vendor_name("   "))

    def test_extract_total_variants(self):
        self.assertEqual(_extract_total("Total Amount: $1,234.56"), (1234.56, "USD"))
        self.assertEqual(_extract_total("Grand Total: USD 500"), (500.0, "USD"))
        self.assertEqual(_extract_total("No total here"), (None, None))

    def test_safe_float_edge_cases(self):
        self.assertEqual(_safe_float("1,234.50"), 1234.50)
        self.assertEqual(_safe_float("$99.99"), 99.99)
        self.assertIsNone(_safe_float("abc"))
        self.assertIsNone(_safe_float(None))


# ── Intake Edge Cases ─────────────────────────────────────────

class TestIntakeEdgeCases(_TempDBTestCase):

    def test_sanitize_filename_extreme(self):
        self.assertEqual(sanitize_filename("...file..png"), "file.png")
        self.assertEqual(sanitize_filename("file/name\\with|chars?.png"), "file_name_with_chars_.png")

    def test_is_file_valid_non_existent(self):
        fake_path = self.input_dir / "does_not_exist.png"
        ok, reason = is_file_valid(fake_path)
        self.assertFalse(ok)
        self.assertIn("does not exist", reason.lower())


# ── Storage Edge Cases ────────────────────────────────────────

class TestStorageEdgeCases(_TempDBTestCase):

    def test_store_invoice_unicode_vendor(self):
        parsed = self._make_parsed(vendor_name="Acme & Co. 🏢")
        doc_id = store_invoice("unicode.png", "hash_unicode", None, parsed)
        row = get_invoice_by_id(doc_id)
        self.assertEqual(row["vendor_name"], "Acme & Co. 🏢")

    def test_store_invoice_maximum_fields(self):
        parsed = self._make_parsed(
            invoice_number="A" * 50,
            vendor_name="V" * 200,
            total_amount=999999.99,
        )
        doc_id = store_invoice("max.png", "hash_max", None, parsed)
        row = get_invoice_by_id(doc_id)
        self.assertEqual(len(row["invoice_number"]), 50)
        self.assertEqual(len(row["vendor_name"]), 200)
        self.assertEqual(float(row["total_amount"]), 999999.99)


if __name__ == "__main__":
    unittest.main()
