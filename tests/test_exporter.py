"""
Tests for exporter module.

Covers:
  - export_to_json
  - export_to_excel
"""

import sys
import unittest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config
from tests.db_setup import init_db_for_test
from src.storage import store_invoice
from src.exporter import export_to_json, export_to_excel


class TestExporter(unittest.TestCase):

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
    def _make_parsed() -> dict:
        return {
            "invoice_number": "INV-EXP-01",
            "invoice_date": "2024-02-10",
            "vendor_name": "Export Corp",
            "total_amount": 2500.00,
            "currency": "USD",
            "line_items": [
                {"description": "Server", "quantity": 1, "unit_price": 2500.00, "line_total": 2500.00}
            ],
            "confidence": "high",
        }

    def test_export_to_json(self):
        doc_id = store_invoice("export_test.png", "hash_exp_1", None, self._make_parsed())
        export_to_json(doc_id)

        json_path = self.output_dir / "json" / f"invoice_{doc_id:03d}.json"
        self.assertTrue(json_path.exists())
        self.assertGreater(json_path.stat().st_size, 0)

    def test_export_to_excel(self):
        store_invoice("export_test2.png", "hash_exp_2", None, self._make_parsed())
        export_to_excel()

        excel_path = self.output_dir / "all_invoices.xlsx"
        self.assertTrue(excel_path.exists())
        self.assertGreater(excel_path.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
