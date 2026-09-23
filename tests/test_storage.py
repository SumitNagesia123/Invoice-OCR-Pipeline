"""
Tests for the storage module.

Covers:
  - Field validation (invoice_number, date, vendor, amount, currency,
    line items, full validate_parsed_data)
  - store_invoice: happy path, status mapping (high → processed,
    medium/low → needs_review), line item insertion, duplicate rejection
  - finalize_processing_log: success and failure paths
  - move_to_processed: basic move and name-collision handling
  - Query helpers: get_invoice_by_id, get_line_items, get_processing_log
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config
from src.db import init_db, get_connection
from src.storage import (
    _validate_invoice_number,
    _validate_date,
    _validate_vendor_name,
    _validate_amount,
    _validate_currency,
    _validate_line_item,
    validate_parsed_data,
    store_invoice,
    finalize_processing_log,
    move_to_processed,
    get_invoice_by_id,
    get_line_items,
    get_processing_log,
)


class _TempDBTestCase(unittest.TestCase):
    """Redirect DB and folders to a temp directory for isolation."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.data_dir = self.tmpdir / "data"
        self.processed_dir = self.tmpdir / "processed_invoices"
        self.input_dir = self.tmpdir / "input_invoices"

        for d in (self.data_dir, self.processed_dir, self.input_dir):
            d.mkdir()

        self.db_path = self.data_dir / "test.db"

        self._patches = [
            patch.object(config, "DATABASE_PATH", self.db_path),
            patch.object(config, "PROCESSED_DIR", self.processed_dir),
            patch.object(config, "INPUT_DIR", self.input_dir),
        ]
        for p in self._patches:
            p.start()

        init_db()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _create_dummy_file(self, name: str = "invoice.png") -> Path:
        """Create a dummy file in the input directory."""
        path = self.input_dir / name
        path.write_text("dummy content", encoding="utf-8")
        return path

    def _insert_processing_log(self, file_name: str = "test.png") -> int:
        """Insert a processing_logs row and return its log_id."""
        conn = get_connection()
        try:
            cur = conn.execute(
                "INSERT INTO processing_logs (file_name, start_time, status) "
                "VALUES (?, datetime('now'), 'running')",
                (file_name,),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

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
        """Build a parsed-data dict matching parse_invoice_text output."""
        if line_items is None:
            line_items = [
                {
                    "description": "Widget",
                    "quantity": 5,
                    "unit_price": 100.00,
                    "line_total": 500.00,
                },
                {
                    "description": "Gadget",
                    "quantity": 2,
                    "unit_price": 250.00,
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


# ── Field validation ─────────────────────────────────────────

class TestValidateInvoiceNumber(unittest.TestCase):

    def test_valid_number(self):
        self.assertEqual(_validate_invoice_number("INV-001"), "INV-001")

    def test_none_returns_none(self):
        self.assertIsNone(_validate_invoice_number(None))

    def test_empty_returns_none(self):
        self.assertIsNone(_validate_invoice_number("  "))

    def test_truncates_long(self):
        long_val = "A" * 100
        result = _validate_invoice_number(long_val)
        self.assertEqual(len(result), 50)

    def test_strips_whitespace(self):
        self.assertEqual(_validate_invoice_number("  INV-002  "), "INV-002")


class TestValidateDate(unittest.TestCase):

    def test_valid_date(self):
        self.assertEqual(_validate_date("2024-01-15"), "2024-01-15")

    def test_none_returns_none(self):
        self.assertIsNone(_validate_date(None))

    def test_bad_format(self):
        self.assertIsNone(_validate_date("01/15/2024"))

    def test_invalid_calendar_date(self):
        self.assertIsNone(_validate_date("2024-02-30"))

    def test_strips_whitespace(self):
        self.assertEqual(_validate_date(" 2024-06-01 "), "2024-06-01")


class TestValidateVendorName(unittest.TestCase):

    def test_valid_name(self):
        self.assertEqual(_validate_vendor_name("Acme Corp"), "Acme Corp")

    def test_none_returns_none(self):
        self.assertIsNone(_validate_vendor_name(None))

    def test_empty_returns_none(self):
        self.assertIsNone(_validate_vendor_name(""))

    def test_truncates_long(self):
        long_val = "X" * 300
        result = _validate_vendor_name(long_val)
        self.assertEqual(len(result), 200)


class TestValidateAmount(unittest.TestCase):

    def test_valid_amount(self):
        self.assertEqual(_validate_amount(1234.56), 1234.56)

    def test_none_returns_none(self):
        self.assertIsNone(_validate_amount(None))

    def test_negative_returns_none(self):
        self.assertIsNone(_validate_amount(-50.0))

    def test_zero_is_valid(self):
        self.assertEqual(_validate_amount(0.0), 0.0)

    def test_rounds_to_two_decimals(self):
        self.assertEqual(_validate_amount(99.999), 100.0)

    def test_string_coercion(self):
        self.assertEqual(_validate_amount("42.50"), 42.50)

    def test_invalid_string_returns_none(self):
        self.assertIsNone(_validate_amount("not a number"))


class TestValidateCurrency(unittest.TestCase):

    def test_valid_currency(self):
        self.assertEqual(_validate_currency("USD"), "USD")

    def test_none_returns_none(self):
        self.assertIsNone(_validate_currency(None))

    def test_lowercase_uppercased(self):
        self.assertEqual(_validate_currency("eur"), "EUR")

    def test_invalid_length(self):
        self.assertIsNone(_validate_currency("US"))

    def test_invalid_chars(self):
        self.assertIsNone(_validate_currency("U$D"))


class TestValidateLineItem(unittest.TestCase):

    def test_valid_item(self):
        item = {
            "description": "Widget",
            "quantity": 5,
            "unit_price": 10.00,
            "line_total": 50.00,
        }
        result = _validate_line_item(item)
        self.assertEqual(result["description"], "Widget")
        self.assertEqual(result["quantity"], 5.0)
        self.assertEqual(result["unit_price"], 10.00)
        self.assertEqual(result["line_total"], 50.00)

    def test_missing_description_returns_none(self):
        item = {"description": None, "quantity": 1}
        self.assertIsNone(_validate_line_item(item))

    def test_empty_description_returns_none(self):
        item = {"description": "  ", "quantity": 1}
        self.assertIsNone(_validate_line_item(item))

    def test_none_quantity_ok(self):
        item = {"description": "Service", "line_total": 100.0}
        result = _validate_line_item(item)
        self.assertEqual(result["description"], "Service")
        self.assertIsNone(result["quantity"])

    def test_negative_quantity_becomes_none(self):
        item = {"description": "Bad qty", "quantity": -3}
        result = _validate_line_item(item)
        self.assertIsNone(result["quantity"])

    def test_truncates_long_description(self):
        item = {"description": "D" * 300, "line_total": 10.0}
        result = _validate_line_item(item)
        self.assertEqual(len(result["description"]), 200)


class TestValidateParsedData(unittest.TestCase):

    def test_full_valid_data(self):
        parsed = {
            "invoice_number": "INV-001",
            "invoice_date": "2024-01-15",
            "vendor_name": "Acme",
            "total_amount": 500.0,
            "currency": "USD",
            "line_items": [
                {"description": "Widget", "quantity": 2,
                 "unit_price": 250.0, "line_total": 500.0},
            ],
            "confidence": "high",
        }
        result = validate_parsed_data(parsed)
        self.assertEqual(result["invoice_number"], "INV-001")
        self.assertEqual(result["invoice_date"], "2024-01-15")
        self.assertEqual(len(result["line_items"]), 1)

    def test_filters_invalid_line_items(self):
        parsed = {
            "line_items": [
                {"description": "Good", "line_total": 10.0},
                {"description": None, "line_total": 5.0},  # invalid
                {"description": "", "line_total": 5.0},    # invalid
            ],
        }
        result = validate_parsed_data(parsed)
        self.assertEqual(len(result["line_items"]), 1)
        self.assertEqual(result["line_items"][0]["description"], "Good")

    def test_empty_input(self):
        result = validate_parsed_data({})
        self.assertIsNone(result["invoice_number"])
        self.assertIsNone(result["invoice_date"])
        self.assertEqual(result["line_items"], [])
        self.assertEqual(result["confidence"], "low")


# ── store_invoice ────────────────────────────────────────────

class TestStoreInvoice(_TempDBTestCase):

    def test_happy_path_high_confidence(self):
        parsed = self._make_parsed(confidence="high")
        doc_id = store_invoice("inv.png", "abc123hash", "/tmp/ocr.txt", parsed)
        self.assertIsInstance(doc_id, int)
        self.assertGreater(doc_id, 0)

        # Verify the row
        row = get_invoice_by_id(doc_id)
        self.assertIsNotNone(row)
        self.assertEqual(row["file_name"], "inv.png")
        self.assertEqual(row["invoice_number"], "INV-001")
        self.assertEqual(row["status"], "processed")

    def test_medium_confidence_needs_review(self):
        parsed = self._make_parsed(confidence="medium")
        doc_id = store_invoice("inv2.png", "def456", None, parsed)
        row = get_invoice_by_id(doc_id)
        self.assertEqual(row["status"], "needs_review")

    def test_low_confidence_needs_review(self):
        parsed = self._make_parsed(confidence="low")
        doc_id = store_invoice("inv3.png", "ghi789", None, parsed)
        row = get_invoice_by_id(doc_id)
        self.assertEqual(row["status"], "needs_review")

    def test_line_items_stored(self):
        parsed = self._make_parsed()
        doc_id = store_invoice("inv4.png", "jkl012", None, parsed)
        items = get_line_items(doc_id)
        self.assertEqual(len(items), 2)
        descriptions = {item["description"] for item in items}
        self.assertEqual(descriptions, {"Widget", "Gadget"})

    def test_no_line_items(self):
        parsed = self._make_parsed(line_items=[])
        doc_id = store_invoice("inv5.png", "mno345", None, parsed)
        items = get_line_items(doc_id)
        self.assertEqual(items, [])

    def test_duplicate_filename_raises(self):
        parsed = self._make_parsed()
        store_invoice("dup.png", "hash1", None, parsed)
        with self.assertRaises(Exception):
            store_invoice("dup.png", "hash2", None, parsed)

    def test_ocr_text_path_stored(self):
        parsed = self._make_parsed()
        doc_id = store_invoice("inv6.png", "pqr678", "/ocr/inv6.txt", parsed)
        row = get_invoice_by_id(doc_id)
        self.assertEqual(row["ocr_text_path"], "/ocr/inv6.txt")

    def test_none_ocr_text_path(self):
        parsed = self._make_parsed()
        doc_id = store_invoice("inv7.png", "stu901", None, parsed)
        row = get_invoice_by_id(doc_id)
        self.assertIsNone(row["ocr_text_path"])

    def test_validated_fields_stored(self):
        """Fields are sanitized before insert (e.g. bad date → None)."""
        parsed = self._make_parsed(invoice_date="not-a-date")
        doc_id = store_invoice("inv8.png", "vwx234", None, parsed)
        row = get_invoice_by_id(doc_id)
        self.assertIsNone(row["invoice_date"])

    def test_invalid_line_items_filtered(self):
        """Line items with no description are dropped before insert."""
        parsed = self._make_parsed(line_items=[
            {"description": "Good One", "quantity": 1,
             "unit_price": 10.0, "line_total": 10.0},
            {"description": None, "quantity": 1,
             "unit_price": 5.0, "line_total": 5.0},
        ])
        doc_id = store_invoice("inv9.png", "yza567", None, parsed)
        items = get_line_items(doc_id)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["description"], "Good One")


# ── finalize_processing_log ──────────────────────────────────

class TestFinalizeProcessingLog(_TempDBTestCase):

    def test_success(self):
        log_id = self._insert_processing_log("test_ok.png")
        finalize_processing_log(log_id, success=True)
        log = get_processing_log(log_id)
        self.assertEqual(log["status"], "success")
        self.assertIsNotNone(log["end_time"])
        self.assertIsNone(log["error_message"])

    def test_failure_with_message(self):
        log_id = self._insert_processing_log("test_fail.png")
        finalize_processing_log(log_id, success=False,
                                error_message="OCR timed out")
        log = get_processing_log(log_id)
        self.assertEqual(log["status"], "failed")
        self.assertEqual(log["error_message"], "OCR timed out")

    def test_failure_without_message(self):
        log_id = self._insert_processing_log("test_fail2.png")
        finalize_processing_log(log_id, success=False)
        log = get_processing_log(log_id)
        self.assertEqual(log["status"], "failed")
        self.assertIsNone(log["error_message"])


# ── move_to_processed ────────────────────────────────────────

class TestMoveToProcessed(_TempDBTestCase):

    def test_basic_move(self):
        src = self._create_dummy_file("done.png")
        dest = move_to_processed(src)
        self.assertTrue(dest.exists())
        self.assertFalse(src.exists())
        self.assertEqual(dest.parent, self.processed_dir)
        self.assertEqual(dest.name, "done.png")

    def test_name_collision_resolved(self):
        src1 = self._create_dummy_file("same.png")
        move_to_processed(src1)
        # Create another file with the same name
        src2 = self._create_dummy_file("same.png")
        dest2 = move_to_processed(src2)
        self.assertTrue(dest2.exists())
        self.assertNotEqual(dest2.name, "same.png")
        self.assertTrue(dest2.name.startswith("same_"))


# ── Query helpers ────────────────────────────────────────────

class TestQueryHelpers(_TempDBTestCase):

    def test_get_invoice_by_id_found(self):
        parsed = self._make_parsed()
        doc_id = store_invoice("q1.png", "hash_q1", None, parsed)
        row = get_invoice_by_id(doc_id)
        self.assertIsNotNone(row)
        self.assertEqual(row["document_id"], doc_id)

    def test_get_invoice_by_id_not_found(self):
        result = get_invoice_by_id(99999)
        self.assertIsNone(result)

    def test_get_line_items_found(self):
        parsed = self._make_parsed()
        doc_id = store_invoice("q2.png", "hash_q2", None, parsed)
        items = get_line_items(doc_id)
        self.assertEqual(len(items), 2)
        for item in items:
            self.assertEqual(item["document_id"], doc_id)

    def test_get_line_items_empty(self):
        items = get_line_items(99999)
        self.assertEqual(items, [])

    def test_get_processing_log_found(self):
        log_id = self._insert_processing_log("q3.png")
        log = get_processing_log(log_id)
        self.assertIsNotNone(log)
        self.assertEqual(log["file_name"], "q3.png")

    def test_get_processing_log_not_found(self):
        result = get_processing_log(99999)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
