"""
Tests for Pydantic schema validation module.

Covers:
  - LineItem auto line_total calculation
  - InvoiceSchema validation and totals consistency
  - validate_invoice helper function
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.schema import LineItem, InvoiceSchema, validate_invoice


class TestSchema(unittest.TestCase):

    def test_line_item_auto_calculation(self):
        item = LineItem(description="Widget", quantity=2.0, unit_price=50.0)
        self.assertEqual(float(item.line_total), 100.0)

    def test_invoice_schema_valid(self):
        data = {
            "file_name": "inv.png",
            "file_hash": "a" * 64,
            "invoice_number": "INV-001",
            "invoice_date": "2026-10-01",
            "vendor_name": "Acme",
            "total_amount": 100.00,
            "currency": "USD",
            "line_items": [
                {"description": "Widget", "quantity": 2.0, "unit_price": 50.0, "line_total": 100.0}
            ],
            "confidence": "high",
        }
        is_valid, error, model = validate_invoice(data)
        self.assertTrue(is_valid)
        self.assertIsNone(error)
        self.assertIsNotNone(model)

    def test_invoice_schema_invalid_total(self):
        data = {
            "file_name": "inv.png",
            "file_hash": "a" * 64,
            "invoice_number": "INV-001",
            "invoice_date": "2026-10-01",
            "vendor_name": "Acme",
            "subtotal": 500.00,  # Mismatched subtotal vs line items sum (100)
            "total_amount": 500.00,
            "currency": "USD",
            "line_items": [
                {"description": "Widget", "quantity": 2.0, "unit_price": 50.0, "line_total": 100.0}
            ],
            "confidence": "high",
        }
        is_valid, error, model = validate_invoice(data)
        self.assertTrue(is_valid)  # Valid Pydantic model creation succeeds, but marks status as needs_review
        self.assertEqual(model.status, "needs_review")


if __name__ == "__main__":
    unittest.main()
