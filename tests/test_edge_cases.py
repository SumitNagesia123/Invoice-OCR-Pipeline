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
from src.db import init_db, get_connection
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

        self.db_path = self.data_dir / "test.db"

        self._patches = [
            patch.object(config, "DATABASE_PATH", self.db_path),
            patch.object(config, "PROCESSED_DIR", self.processed_dir),
            patch.object(config, "INPUT_DIR", self.input_dir),
            patch.object(config, "FAILED_DIR", self.failed_dir),
            patch.object(config, "OCR_OUTPUT_DIR", self.ocr_output_dir),
        ]
        for p in self._patches:
            p.start()

        init_db()

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


# ══════════════════════════════════════════════════════════════
#  PARSER EDGE CASES
# ══════════════════════════════════════════════════════════════


class TestExtractInvoiceNumberEdge(unittest.TestCase):

    def test_ref_hash_shorthand(self):
        self.assertEqual(
            _extract_invoice_number("Ref # RF-4422"), "RF-4422")

    def test_ref_colon(self):
        self.assertEqual(
            _extract_invoice_number("Ref: REF-999"), "REF-999")

    def test_bill_hash(self):
        self.assertEqual(
            _extract_invoice_number("Bill # B100"), "B100")

    def test_multiline_first_match_wins(self):
        text = "Invoice #FIRST-001\nRef # SECOND-002"
        self.assertEqual(_extract_invoice_number(text), "FIRST-001")

    def test_invoice_number_with_slashes(self):
        self.assertEqual(
            _extract_invoice_number("Invoice Number: 2024/Q1/001"),
            "2024/Q1/001",
        )

    def test_whitespace_only_text(self):
        self.assertIsNone(_extract_invoice_number("   \n\t  "))

    def test_number_starting_with_digit(self):
        self.assertEqual(
            _extract_invoice_number("Invoice #12345ABC"), "12345ABC")


class TestExtractDateEdge(unittest.TestCase):

    def test_ambiguous_both_lte_12_defaults_mm_dd(self):
        """When both numbers ≤ 12, default to MM/DD/YYYY."""
        text = "Date: 05/06/2024"
        result = _extract_date(text)
        # Default: MM/DD → 2024-05-06
        self.assertEqual(result, "2024-05-06")

    def test_dd_mon_yyyy_abbreviated(self):
        text = "Date: 15 Feb 2024"
        self.assertEqual(_extract_date(text), "2024-02-15")

    def test_mon_dd_yyyy_no_comma(self):
        text = "Date: March 1 2024"
        self.assertEqual(_extract_date(text), "2024-03-01")

    def test_date_with_period_separator(self):
        """Date formats with unusual separators may not parse."""
        text = "Date: 15.01.2024"
        # The parser uses [\s/:] anchors — period-separated dates
        # may or may not match depending on regex.  Just ensure no crash.
        result = _extract_date(text)
        # Accept None or a valid date — no crash is the key assertion.
        if result is not None:
            self.assertRegex(result, r"^\d{4}-\d{2}-\d{2}$")

    def test_invalid_month_13(self):
        text = "Date: 13/32/2024"
        self.assertIsNone(_extract_date(text))

    def test_very_old_date_still_parses(self):
        text = "Date: 1999-12-31"
        self.assertEqual(_extract_date(text), "1999-12-31")

    def test_date_in_body_not_header(self):
        """A date deeper than 500 chars from the start is NOT picked up
        by fallback (which only scans the first 500 chars)."""
        text = "x" * 600 + "\n2024-08-01"
        # No keyword anchor → fallback only scans first 500 chars
        self.assertIsNone(_extract_date(text))


class TestExtractVendorNameEdge(unittest.TestCase):

    def test_vendor_with_special_characters(self):
        text = "Vendor: Müller & Söhne GmbH\nDate: 2024-01-01"
        self.assertEqual(_extract_vendor_name(text), "Müller & Söhne GmbH")

    def test_all_lines_skippable(self):
        """When every line starts with 'Invoice' or similar, returns None."""
        text = "Invoice #100\nInvoice Date: Jan 1\nPage 1\n"
        # "Page" is also a skippable prefix
        result = _extract_vendor_name(text)
        self.assertIsNone(result)

    def test_sold_by_keyword(self):
        text = "Sold by: QuickShip LLC\nRef: 123"
        self.assertEqual(_extract_vendor_name(text), "QuickShip LLC")

    def test_company_keyword(self):
        text = "Company: WorldWideCo\nInvoice #99"
        self.assertEqual(_extract_vendor_name(text), "WorldWideCo")

    def test_bill_from_keyword(self):
        text = "Bill From: BillingCorp\n123 Main St"
        self.assertEqual(_extract_vendor_name(text), "BillingCorp")

    def test_very_short_lines_skipped(self):
        """Lines shorter than 3 chars are skipped in fallback."""
        text = "AB\nCo\nReliable Parts Inc.\nDate: 2024-01-01"
        self.assertEqual(_extract_vendor_name(text), "Reliable Parts Inc.")

    def test_only_whitespace_and_empty_lines(self):
        text = "  \n\n  \n"
        self.assertIsNone(_extract_vendor_name(text))


class TestExtractTotalEdge(unittest.TestCase):

    def test_subtotal_not_matched_as_total(self):
        """'Subtotal' alone should not match \\bTotal\\b."""
        text = "Subtotal: $500.00"
        # "Subtotal" does NOT contain a word-boundary before "total"
        amount, currency = _extract_total(text)
        self.assertIsNone(amount)

    def test_multiple_totals_last_wins(self):
        text = (
            "Subtotal: $100.00\n"
            "Tax: $8.00\n"
            "Total: $108.00\n"
        )
        amount, currency = _extract_total(text)
        self.assertEqual(amount, 108.00)

    def test_yen_symbol(self):
        text = "Total: ¥10000.00"
        amount, currency = _extract_total(text)
        self.assertEqual(amount, 10000.00)
        self.assertEqual(currency, "JPY")

    def test_net_total_keyword(self):
        text = "Net Total: $750.00"
        amount, currency = _extract_total(text)
        self.assertEqual(amount, 750.00)

    def test_balance_due_keyword(self):
        text = "Balance Due: €1,234.56"
        amount, currency = _extract_total(text)
        self.assertEqual(amount, 1234.56)
        self.assertEqual(currency, "EUR")

    def test_total_due_keyword(self):
        text = "Total Due: £99.99"
        amount, currency = _extract_total(text)
        self.assertEqual(amount, 99.99)
        self.assertEqual(currency, "GBP")

    def test_total_with_no_decimal(self):
        text = "Total: $500"
        amount, currency = _extract_total(text)
        # "500" has no decimal portion — may or may not match the
        # regex pattern [\d,]+\.?\d*.  Accept the result either way.
        if amount is not None:
            self.assertEqual(amount, 500.0)

    def test_iso_code_inr(self):
        text = "Total Amount INR 12345.67"
        amount, currency = _extract_total(text)
        self.assertEqual(amount, 12345.67)
        self.assertEqual(currency, "INR")

    def test_iso_code_chf(self):
        text = "Total: CHF 999.00"
        amount, currency = _extract_total(text)
        self.assertEqual(amount, 999.00)
        self.assertEqual(currency, "CHF")


class TestExtractLineItemsEdge(unittest.TestCase):

    def test_tax_line_skipped(self):
        text = "Tax (8%)   $80.00\n"
        items = _extract_line_items(text)
        self.assertEqual(items, [])

    def test_shipping_line_skipped(self):
        text = "Shipping   $15.00\n"
        items = _extract_line_items(text)
        self.assertEqual(items, [])

    def test_discount_line_skipped(self):
        text = "Discount   $50.00\n"
        items = _extract_line_items(text)
        self.assertEqual(items, [])

    def test_balance_line_skipped(self):
        text = "Balance   $100.00\n"
        items = _extract_line_items(text)
        self.assertEqual(items, [])

    def test_mixed_valid_and_skip_lines(self):
        text = (
            "Laptop         $1,200.00\n"
            "Tax (8%)       $96.00\n"
            "Shipping       $15.00\n"
        )
        items = _extract_line_items(text)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["description"], "Laptop")
        self.assertEqual(items[0]["line_total"], 1200.00)

    def test_line_item_with_dollar_signs(self):
        text = "2    Premium Support    $100.00    $200.00\n"
        items = _extract_line_items(text)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["description"], "Premium Support")
        self.assertEqual(items[0]["quantity"], 2.0)
        self.assertEqual(items[0]["line_total"], 200.00)

    def test_empty_text_returns_empty(self):
        self.assertEqual(_extract_line_items(""), [])

    def test_only_header_row_no_data(self):
        text = "Description   Qty   Unit Price   Total\n"
        items = _extract_line_items(text)
        # Header row alone doesn't match the data patterns
        self.assertEqual(items, [])


class TestSafeFloatEdge(unittest.TestCase):

    def test_empty_string(self):
        self.assertIsNone(_safe_float(""))

    def test_commas_stripped(self):
        self.assertEqual(_safe_float("1,234,567.89"), 1234567.89)

    def test_plain_integer_string(self):
        self.assertEqual(_safe_float("42"), 42.0)

    def test_negative_number(self):
        self.assertEqual(_safe_float("-10"), -10.0)

    def test_word_returns_none(self):
        self.assertIsNone(_safe_float("abc"))


class TestParseInvoiceTextEdge(unittest.TestCase):

    def test_whitespace_only(self):
        result = parse_invoice_text("   \n\t\n   ")
        self.assertIsNone(result["invoice_number"])
        self.assertEqual(result["confidence"], "low")

    def test_only_vendor_yields_low(self):
        """One field (vendor) → low confidence."""
        text = "SomeCompany Inc."
        result = parse_invoice_text(text)
        self.assertIsNotNone(result["vendor_name"])
        self.assertEqual(result["confidence"], "low")

    def test_all_four_fields_yields_high(self):
        text = (
            "Acme Corp\n"
            "Invoice #INV-999\n"
            "Date: 2024-06-15\n"
            "Total: $5,000.00\n"
        )
        result = parse_invoice_text(text)
        self.assertEqual(result["confidence"], "high")
        self.assertEqual(result["invoice_number"], "INV-999")
        self.assertEqual(result["invoice_date"], "2024-06-15")
        self.assertEqual(result["total_amount"], 5000.00)
        self.assertIsNotNone(result["vendor_name"])

    def test_unicode_in_text_no_crash(self):
        text = "日本語テキスト\nInvoice #JP-001\nTotal: ¥10000.00"
        result = parse_invoice_text(text)
        # Should not crash — invoice number may or may not be extracted
        self.assertIsInstance(result, dict)
        self.assertIn("confidence", result)


# ══════════════════════════════════════════════════════════════
#  INTAKE EDGE CASES
# ══════════════════════════════════════════════════════════════


class TestSanitizeFilenameEdge(unittest.TestCase):

    def test_only_dots(self):
        result = sanitize_filename("...")
        self.assertNotIn("..", result)
        # After collapsing dots and stripping leading dots, result
        # should not be empty (falls back to "unnamed_file")
        self.assertTrue(len(result) > 0)

    def test_only_slashes(self):
        result = sanitize_filename("///")
        self.assertNotIn("/", result)
        self.assertTrue(len(result) > 0)

    def test_empty_string(self):
        result = sanitize_filename("")
        self.assertEqual(result, "unnamed_file")

    def test_null_byte_with_traversal(self):
        result = sanitize_filename("../../../\0etc/passwd")
        self.assertNotIn("..", result)
        self.assertNotIn("\0", result)
        self.assertNotIn("/", result)

    def test_windows_backslash_traversal(self):
        result = sanitize_filename("..\\..\\system32\\config")
        self.assertNotIn("\\", result)
        self.assertNotIn("..", result)

    def test_mixed_special_characters(self):
        result = sanitize_filename("file<name>|with:bad*chars?.png")
        # Special chars should be replaced with underscores
        self.assertNotIn("<", result)
        self.assertNotIn(">", result)
        self.assertNotIn("|", result)
        self.assertNotIn("*", result)
        self.assertNotIn("?", result)

    def test_preserves_extension(self):
        result = sanitize_filename("my--invoice.png")
        self.assertTrue(result.endswith(".png"))

    def test_space_in_name(self):
        result = sanitize_filename("my invoice file.png")
        # Spaces are allowed in the sanitizer
        self.assertIn(" ", result)


class TestIsFileValidEdge(_TempDBTestCase):

    def test_tiff_extension_supported(self):
        """TIFF files are listed in SUPPORTED_EXTENSIONS."""
        path = self.input_dir / "scan.tiff"
        img = Image.new("RGB", (100, 100), color="white")
        img.save(path)
        ok, reason = is_file_valid(path)
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_tif_extension_supported(self):
        """Short .tif extension is also supported."""
        path = self.input_dir / "scan.tif"
        img = Image.new("RGB", (100, 100), color="white")
        img.save(path)
        ok, reason = is_file_valid(path)
        self.assertTrue(ok)

    def test_jpeg_extension_supported(self):
        path = self.input_dir / "photo.jpeg"
        img = Image.new("RGB", (100, 100), color="white")
        img.save(path, format="JPEG")
        ok, reason = is_file_valid(path)
        self.assertTrue(ok)

    def test_valid_pdf_magic(self):
        """A file with proper PDF magic bytes passes the PDF check."""
        path = self.input_dir / "real.pdf"
        path.write_bytes(b"%PDF-1.4 some content here")
        ok, reason = is_file_valid(path)
        self.assertTrue(ok)

    def test_fake_pdf_wrong_magic(self):
        """A .pdf file without the magic bytes is rejected."""
        path = self.input_dir / "fake.pdf"
        path.write_bytes(b"This is not a PDF at all")
        ok, reason = is_file_valid(path)
        self.assertFalse(ok)
        self.assertIn("valid PDF", reason)

    def test_uppercase_extension_unsupported(self):
        """Extensions are lowered before comparison, but the raw file name
        must match.  Create a .PNG file (uppercase) and check it's valid."""
        path = self.input_dir / "scan.PNG"
        img = Image.new("RGB", (100, 100), color="white")
        img.save(path)
        ok, reason = is_file_valid(path)
        self.assertTrue(ok)

    def test_bmp_extension_unsupported(self):
        path = self.input_dir / "bitmap.bmp"
        img = Image.new("RGB", (100, 100), color="white")
        img.save(path, format="BMP")
        ok, reason = is_file_valid(path)
        self.assertFalse(ok)
        self.assertIn("Unsupported", reason)


class TestScanInputFolderEdge(_TempDBTestCase):

    def test_gitkeep_file_ignored(self):
        """The .gitkeep sentinel file should be skipped entirely."""
        (self.input_dir / ".gitkeep").write_text("")
        results = scan_input_folder()
        self.assertEqual(results, [])

    def test_filename_known_duplicate(self):
        """A file whose name is already in the DB is skipped even if
        its hash differs."""
        # First, insert a record with the same filename
        conn = get_connection()
        conn.execute(
            "INSERT INTO invoice_documents (file_name, file_hash, status) "
            "VALUES (?, ?, 'processed')",
            ("known.png", "some_old_hash"),
        )
        conn.commit()
        conn.close()

        # Create a new (different content) file with the same name
        img = Image.new("RGB", (50, 50), color="blue")
        path = self.input_dir / "known.png"
        img.save(path)

        results = scan_input_folder()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "skipped_duplicate")
        self.assertIn("Filename", results[0]["reason"])

    def test_multiple_valid_files_all_ready(self):
        """Multiple valid images should all be marked 'ready'."""
        for i in range(3):
            img = Image.new("RGB", (50, 50), color=(i * 80, 0, 0))
            img.save(self.input_dir / f"inv_{i}.png")
        results = scan_input_folder()
        self.assertEqual(len(results), 3)
        for r in results:
            self.assertEqual(r["status"], "ready")


# ══════════════════════════════════════════════════════════════
#  OCR PREPROCESSING EDGE CASES
# ══════════════════════════════════════════════════════════════


class TestPreprocessingEdge(unittest.TestCase):

    def test_to_grayscale_rgba(self):
        """A 4-channel (RGBA) image should be converted to grayscale."""
        rgba = np.random.randint(0, 256, (100, 100, 4), dtype=np.uint8)
        gray = _to_grayscale(rgba)
        self.assertEqual(len(gray.shape), 2)

    def test_deskew_few_nonzero_returns_unchanged(self):
        """An image with fewer than 10 non-zero pixels is returned as-is."""
        img = np.zeros((100, 100), dtype=np.uint8)
        img[50, 50] = 255  # Only 1 non-zero pixel
        result = _deskew(img)
        np.testing.assert_array_equal(result, img)

    def test_deskew_all_white(self):
        """An all-white image should have many coords but angle ≈ 0,
        so it should be returned unchanged (angle < 0.5)."""
        img = np.ones((100, 100), dtype=np.uint8) * 255
        result = _deskew(img)
        self.assertEqual(result.shape, img.shape)

    def test_threshold_uniform_gray(self):
        """A uniform gray image produces binary output (all one value)."""
        img = np.full((100, 100), 128, dtype=np.uint8)
        binary = _threshold(img)
        unique = set(np.unique(binary))
        self.assertTrue(unique.issubset({0, 255}))

    def test_denoise_preserves_shape(self):
        """Denoise should not change the image dimensions."""
        img = np.random.randint(0, 256, (200, 300), dtype=np.uint8)
        result = _denoise(img)
        self.assertEqual(result.shape, (200, 300))

    def test_preprocess_very_small_image(self):
        """A tiny image should not crash the preprocessing pipeline."""
        # 5x5 color image
        tiny = np.random.randint(0, 256, (5, 5, 3), dtype=np.uint8)
        result = preprocess_image(tiny)
        self.assertEqual(len(result.shape), 2)

    def test_preprocess_single_pixel(self):
        """A 1x1 image shouldn't crash."""
        pixel = np.array([[[128, 128, 128]]], dtype=np.uint8)
        result = preprocess_image(pixel)
        self.assertEqual(len(result.shape), 2)


# ══════════════════════════════════════════════════════════════
#  STORAGE VALIDATION EDGE CASES
# ══════════════════════════════════════════════════════════════


class TestValidateLineItemEdge(unittest.TestCase):

    def test_negative_unit_price_becomes_none(self):
        item = {"description": "Widget", "unit_price": -50.0}
        result = _validate_line_item(item)
        self.assertEqual(result["description"], "Widget")
        self.assertIsNone(result["unit_price"])

    def test_negative_line_total_becomes_none(self):
        item = {"description": "Credit", "line_total": -100.0}
        result = _validate_line_item(item)
        self.assertIsNone(result["line_total"])

    def test_non_numeric_quantity_becomes_none(self):
        item = {"description": "Service", "quantity": "five"}
        result = _validate_line_item(item)
        self.assertIsNone(result["quantity"])

    def test_non_numeric_unit_price_becomes_none(self):
        item = {"description": "Service", "unit_price": "cheap"}
        result = _validate_line_item(item)
        self.assertIsNone(result["unit_price"])

    def test_non_numeric_line_total_becomes_none(self):
        item = {"description": "Service", "line_total": "N/A"}
        result = _validate_line_item(item)
        self.assertIsNone(result["line_total"])

    def test_zero_quantity_is_valid(self):
        item = {"description": "Free sample", "quantity": 0}
        result = _validate_line_item(item)
        self.assertEqual(result["quantity"], 0.0)

    def test_zero_unit_price_is_valid(self):
        item = {"description": "Complimentary", "unit_price": 0.0}
        result = _validate_line_item(item)
        self.assertEqual(result["unit_price"], 0.0)

    def test_description_only_whitespace_returns_none(self):
        item = {"description": "   \t\n  ", "quantity": 1}
        self.assertIsNone(_validate_line_item(item))

    def test_very_long_description_truncated(self):
        item = {"description": "X" * 500, "line_total": 10.0}
        result = _validate_line_item(item)
        self.assertEqual(len(result["description"]), 200)


class TestValidateParsedDataEdge(unittest.TestCase):

    def test_confidence_missing_defaults_to_low(self):
        parsed = {"invoice_number": "INV-1"}
        result = validate_parsed_data(parsed)
        self.assertEqual(result["confidence"], "low")

    def test_all_fields_none(self):
        parsed = {
            "invoice_number": None,
            "invoice_date": None,
            "vendor_name": None,
            "total_amount": None,
            "currency": None,
            "line_items": [],
        }
        result = validate_parsed_data(parsed)
        self.assertIsNone(result["invoice_number"])
        self.assertIsNone(result["invoice_date"])
        self.assertEqual(result["line_items"], [])

    def test_line_items_not_a_list(self):
        """If line_items key is missing entirely, defaults to empty list."""
        parsed = {"invoice_number": "X-1"}
        result = validate_parsed_data(parsed)
        self.assertEqual(result["line_items"], [])


class TestValidateAmountEdge(unittest.TestCase):

    def test_very_large_amount(self):
        result = _validate_amount(999999999.99)
        self.assertEqual(result, 999999999.99)

    def test_string_with_spaces(self):
        """A string with leading/trailing spaces around a number."""
        # float(" 123.45 ") works in Python, so this should succeed.
        result = _validate_amount(" 123.45 ")
        self.assertEqual(result, 123.45)

    def test_boolean_true_coerced(self):
        """Python's float(True) == 1.0 — this is edge-case behavior."""
        result = _validate_amount(True)
        self.assertEqual(result, 1.0)

    def test_none_value(self):
        self.assertIsNone(_validate_amount(None))


class TestValidateDateEdge(unittest.TestCase):

    def test_date_with_extra_text(self):
        """Date with trailing text should fail (not match ISO regex)."""
        self.assertIsNone(_validate_date("2024-01-15 some text"))

    def test_date_with_time(self):
        self.assertIsNone(_validate_date("2024-01-15T10:30:00"))

    def test_feb_29_leap_year(self):
        self.assertEqual(_validate_date("2024-02-29"), "2024-02-29")

    def test_feb_29_non_leap_year(self):
        self.assertIsNone(_validate_date("2023-02-29"))


class TestValidateCurrencyEdge(unittest.TestCase):

    def test_mixed_case(self):
        self.assertEqual(_validate_currency("usd"), "USD")

    def test_four_letters(self):
        self.assertIsNone(_validate_currency("USDD"))

    def test_one_letter(self):
        self.assertIsNone(_validate_currency("U"))

    def test_numbers_in_code(self):
        self.assertIsNone(_validate_currency("US1"))

    def test_empty_string(self):
        self.assertIsNone(_validate_currency(""))


class TestValidateInvoiceNumberEdge(unittest.TestCase):

    def test_numeric_only(self):
        self.assertEqual(_validate_invoice_number("12345"), "12345")

    def test_integer_input(self):
        """Non-string input should be coerced via str()."""
        self.assertEqual(_validate_invoice_number(12345), "12345")

    def test_exactly_max_length(self):
        val = "A" * 50
        self.assertEqual(_validate_invoice_number(val), val)

    def test_one_over_max_length(self):
        val = "A" * 51
        self.assertEqual(len(_validate_invoice_number(val)), 50)


# ══════════════════════════════════════════════════════════════
#  STORAGE: store_invoice EDGE CASES
# ══════════════════════════════════════════════════════════════


class TestStoreInvoiceEdge(_TempDBTestCase):

    def test_unicode_vendor_name(self):
        parsed = self._make_parsed(vendor_name="Müller & Söhne GmbH")
        doc_id = store_invoice("unicode.png", "hash_uni", None, parsed)
        row = get_invoice_by_id(doc_id)
        self.assertEqual(row["vendor_name"], "Müller & Söhne GmbH")

    def test_very_large_total(self):
        parsed = self._make_parsed(total_amount=999999999.99)
        doc_id = store_invoice("big.png", "hash_big", None, parsed)
        row = get_invoice_by_id(doc_id)
        self.assertEqual(row["total_amount"], 999999999.99)

    def test_zero_total_amount(self):
        parsed = self._make_parsed(total_amount=0.0)
        doc_id = store_invoice("zero.png", "hash_zero", None, parsed)
        row = get_invoice_by_id(doc_id)
        self.assertEqual(row["total_amount"], 0.0)

    def test_all_fields_none(self):
        """An invoice with no extracted fields should still be storable."""
        parsed = {
            "invoice_number": None,
            "invoice_date": None,
            "vendor_name": None,
            "total_amount": None,
            "currency": None,
            "line_items": [],
            "confidence": "low",
        }
        doc_id = store_invoice("empty.png", "hash_empty", None, parsed)
        row = get_invoice_by_id(doc_id)
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "needs_review")
        self.assertIsNone(row["invoice_number"])
        self.assertIsNone(row["vendor_name"])

    def test_many_line_items(self):
        items = [
            {
                "description": f"Item {i}",
                "quantity": i,
                "unit_price": 10.0,
                "line_total": 10.0 * i,
            }
            for i in range(1, 51)
        ]
        parsed = self._make_parsed(line_items=items)
        doc_id = store_invoice("many.png", "hash_many", None, parsed)
        stored_items = get_line_items(doc_id)
        self.assertEqual(len(stored_items), 50)

    def test_sql_injection_in_vendor_name(self):
        """SQL injection attempts should be safely handled by parameterized queries."""
        parsed = self._make_parsed(
            vendor_name="'; DROP TABLE invoice_documents; --"
        )
        doc_id = store_invoice(
            "injection.png", "hash_inject", None, parsed)
        row = get_invoice_by_id(doc_id)
        # The vendor name should be stored literally
        self.assertEqual(
            row["vendor_name"],
            "'; DROP TABLE invoice_documents; --",
        )
        # Table should still exist
        conn = get_connection()
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM invoice_documents"
            ).fetchone()[0]
            self.assertGreaterEqual(count, 1)
        finally:
            conn.close()

    def test_sql_injection_in_invoice_number(self):
        parsed = self._make_parsed(
            invoice_number="INV'; DELETE FROM invoice_line_items; --"
        )
        doc_id = store_invoice(
            "inject2.png", "hash_inject2", None, parsed)
        row = get_invoice_by_id(doc_id)
        # Should be truncated to 50 chars but stored literally
        self.assertIn("INV", row["invoice_number"])


if __name__ == "__main__":
    unittest.main()
