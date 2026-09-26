"""
Integration tests for the full InvoiceOCR pipeline.

Exercises the entire flow:
  intake → OCR → parse → store → (log/move)
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config
from src.db import init_db, get_connection
from src import intake, ocr, parser, storage

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

    def _create_synthetic_invoice(self, text: str, name: str = "invoice.png") -> Path:
        """Create a PNG with text drawn on it."""
        from PIL import ImageFont
        img = Image.new("RGB", (1200, 600), color="white")
        draw = ImageDraw.Draw(img)
        # Try to use a larger font for readability
        try:
            # On Windows, arial.ttf is usually available
            font = ImageFont.truetype("arial.ttf", 36)
        except OSError:
            font = ImageFont.load_default()

        draw.text((40, 40), text, fill="black", font=font)
        path = self.input_dir / name
        img.save(path)
        return path

class TestFullPipelineIntegration(_TempDBTestCase):

    def test_pipeline_happy_path(self):
        # 1. Create invoice image
        text = (
            "Acme Corp\n"
            "Invoice #INV-001\n"
            "Date: 2024-01-15\n"
            "Widget   1   100.00   100.00\n"
            "Total: $100.00"
        )
        invoice_path = self._create_synthetic_invoice(text, "inv1.png")

        # 2. Intake
        intake_results = intake.scan_input_folder()
        self.assertEqual(len(intake_results), 1)
        result = intake_results[0]
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["file_name"], "inv1.png")

        # 3. OCR
        ocr_result = ocr.extract_text(result["file_path"])
        self.assertTrue(ocr_result["success"])

        # 4. Parse
        parsed_data = parser.parse_invoice_text(ocr_result["text"])
        self.assertEqual(parsed_data["invoice_number"], "INV-001")

        # 5. Store
        doc_id = storage.store_invoice(
            result["file_name"],
            result["file_hash"],
            ocr_result["ocr_text_path"],
            parsed_data
        )
        self.assertGreater(doc_id, 0)

        # 6. Finalize log & Move
        storage.finalize_processing_log(result["log_id"], success=True)
        storage.move_to_processed(invoice_path)

        # 7. Verification
        invoice = storage.get_invoice_by_id(doc_id)
        self.assertIsNotNone(invoice)
        self.assertEqual(invoice["status"], "processed")

        line_items = storage.get_line_items(doc_id)
        self.assertEqual(len(line_items), 1)
        self.assertEqual(line_items[0]["description"], "Widget")

    def test_pipeline_duplicate_rejection(self):
        # Create invoice
        text = "Invoice #INV-001"
        invoice_path = self._create_synthetic_invoice(text, "dup.png")

        # First Intake - Process it fully to store in DB
        intake_results = intake.scan_input_folder()
        result = intake_results[0]

        ocr_result = ocr.extract_text(result["file_path"])
        parsed_data = parser.parse_invoice_text(ocr_result["text"])

        storage.store_invoice(
            result["file_name"],
            result["file_hash"],
            ocr_result["ocr_text_path"],
            parsed_data
        )
        storage.finalize_processing_log(result["log_id"], success=True)
        # Move to processed so it's not in input_dir
        storage.move_to_processed(invoice_path)

        # Create the same file again in input_dir
        self._create_synthetic_invoice(text, "dup.png")

        # Second Intake should detect duplicate
        intake_results = intake.scan_input_folder()
        self.assertEqual(len(intake_results), 1)                # It finds it
        self.assertEqual(intake_results[0]["status"], "skipped_duplicate") # It should be marked skipped
        self.assertIn("Duplicate", intake_results[0]["reason"])

    def test_pipeline_corrupt_file_handling(self):
        # Create non-image file
        path = self.input_dir / "bad.png"
        path.write_text("not an image")

        intake_results = intake.scan_input_folder()
        self.assertEqual(len(intake_results), 1)
        result = intake_results[0]

        # OCR should fail
        ocr_result = ocr.extract_text(result["file_path"])
        self.assertFalse(ocr_result["success"])

        # Finalize as failure
        storage.finalize_processing_log(result["log_id"], success=False, error_message=ocr_result["error"])

        # Verify log
        log = storage.get_processing_log(result["log_id"])
        self.assertEqual(log["status"], "failed")

if __name__ == "__main__":
    unittest.main()
