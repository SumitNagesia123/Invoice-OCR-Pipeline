"""
Tests for the reporting module.

Covers:
  - generate_report_data: data gathering, status counts, duplicates
  - create_excel: multi-sheet Excel generation
  - create_html: HTML report generation
  - run_reporter: end-to-end execution
"""

import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config
from tests.db_setup import init_db_for_test
from src.storage import store_invoice
from src.reporter import (
    generate_report_data,
    create_excel,
    create_html,
    run_reporter,
)


class TestReporter(unittest.TestCase):
    """Test suite for reporter module using MySQL test database."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.output_dir = self.tmpdir / "output"
        self.output_dir.mkdir()

        self._patches = [
            patch.object(config, "PROJECT_ROOT", self.tmpdir),
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

    def test_generate_report_data_empty(self):
        since = datetime.now(timezone.utc) - timedelta(hours=1)
        until = datetime.now(timezone.utc) + timedelta(hours=1)
        data = generate_report_data(since, until)
        self.assertEqual(data["total"], 0)
        self.assertEqual(data["success"], 0)
        self.assertEqual(data["needs_review"], 0)
        self.assertEqual(data["failed"], 0)
        self.assertEqual(data["duplicates"], 0)
        self.assertTrue(data["invoices"].empty)
        self.assertTrue(data["line_items"].empty)

    def test_generate_report_data_with_invoices(self):
        store_invoice("inv1.png", "hash1", None, self._make_parsed(confidence="high"))
        store_invoice("inv2.png", "hash2", None, self._make_parsed(confidence="low"))

        since = datetime.now(timezone.utc) - timedelta(hours=1)
        until = datetime.now(timezone.utc) + timedelta(hours=1)
        data = generate_report_data(since, until)

        self.assertEqual(data["total"], 2)
        self.assertEqual(data["success"], 1)
        self.assertEqual(data["needs_review"], 1)
        self.assertEqual(len(data["invoices"]), 2)
        self.assertEqual(len(data["line_items"]), 2)

    def test_create_excel(self):
        store_invoice("inv1.png", "hash1", None, self._make_parsed())
        since = datetime.now(timezone.utc) - timedelta(hours=1)
        until = datetime.now(timezone.utc) + timedelta(hours=1)
        data = generate_report_data(since, until)

        excel_path = self.output_dir / "report.xlsx"
        create_excel(data, excel_path)
        self.assertTrue(excel_path.exists())
        self.assertGreater(excel_path.stat().st_size, 0)

    def test_create_html(self):
        store_invoice("inv1.png", "hash1", None, self._make_parsed())
        since = datetime.now(timezone.utc) - timedelta(hours=1)
        until = datetime.now(timezone.utc) + timedelta(hours=1)
        data = generate_report_data(since, until)

        html = create_html(data)
        self.assertIn("Invoice Processing Report", html)
        self.assertIn("Total: 1", html)

    def test_run_reporter(self):
        store_invoice("inv1.png", "hash1", None, self._make_parsed())
        since = datetime.now(timezone.utc) - timedelta(hours=1)
        until = datetime.now(timezone.utc) + timedelta(hours=1)

        excel_path, html = run_reporter(since, until)
        self.assertTrue(excel_path.exists())
        self.assertIn("Invoice Processing Report", html)


if __name__ == "__main__":
    unittest.main()
