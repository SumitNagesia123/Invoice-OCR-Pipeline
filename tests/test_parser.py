"""
Tests for the structured parsing module.

Covers:
  - Invoice number extraction (various label styles)
  - Date extraction (multiple formats + keyword anchors)
  - Vendor name extraction (keyword anchor + first-line fallback)
  - Total / currency extraction
  - Line-item extraction (4-col and 2-col formats)
  - Full parse_invoice_text integration (high / medium / low confidence)
  - Edge cases: empty text, garbage text, partial fields
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.parser import (
    _extract_invoice_number,
    _extract_date,
    _extract_vendor_name,
    _extract_total,
    _extract_line_items,
    parse_invoice_text,
)


# ── Invoice number ───────────────────────────────────────────

class TestExtractInvoiceNumber(unittest.TestCase):

    def test_invoice_hash(self):
        self.assertEqual(
            _extract_invoice_number("Invoice #12345"), "12345")

    def test_invoice_no_colon(self):
        self.assertEqual(
            _extract_invoice_number("Invoice No: ABC-789"), "ABC-789")

    def test_invoice_number_label(self):
        self.assertEqual(
            _extract_invoice_number("Invoice Number: INV-2024-001"),
            "INV-2024-001",
        )

    def test_inv_shorthand(self):
        self.assertEqual(
            _extract_invoice_number("Inv # 9900"), "9900")

    def test_reference_number(self):
        self.assertEqual(
            _extract_invoice_number("Reference: REF001"), "REF001")

    def test_bill_number(self):
        self.assertEqual(
            _extract_invoice_number("Bill No 7777"), "7777")

    def test_no_match(self):
        self.assertIsNone(
            _extract_invoice_number("Hello World, no invoice here"))

    def test_case_insensitive(self):
        self.assertEqual(
            _extract_invoice_number("INVOICE NUMBER: X42"), "X42")


# ── Date extraction ──────────────────────────────────────────

class TestExtractDate(unittest.TestCase):

    def test_mm_dd_yyyy(self):
        text = "Invoice Date: 01/15/2024"
        self.assertEqual(_extract_date(text), "2024-01-15")

    def test_yyyy_mm_dd(self):
        text = "Date: 2024-01-15"
        self.assertEqual(_extract_date(text), "2024-01-15")

    def test_dd_mon_yyyy(self):
        text = "Date: 15 January 2024"
        self.assertEqual(_extract_date(text), "2024-01-15")

    def test_mon_dd_yyyy(self):
        text = "Date: Jan 15, 2024"
        self.assertEqual(_extract_date(text), "2024-01-15")

    def test_dd_mm_yyyy_day_gt_12(self):
        """When first number > 12, it must be the day → DD/MM/YYYY."""
        text = "Date: 25/06/2024"
        self.assertEqual(_extract_date(text), "2024-06-25")

    def test_billing_date_label(self):
        text = "Billing Date: 03/20/2024"
        self.assertEqual(_extract_date(text), "2024-03-20")

    def test_no_date_found(self):
        self.assertIsNone(_extract_date("No date in this text at all"))

    def test_fallback_no_keyword(self):
        """A date without a keyword anchor should still be found."""
        text = "Some header\n2024-07-04\nsome body text"
        self.assertEqual(_extract_date(text), "2024-07-04")


# ── Vendor name ──────────────────────────────────────────────

class TestExtractVendorName(unittest.TestCase):

    def test_from_keyword(self):
        text = "From: Acme Corp\nInvoice #123"
        self.assertEqual(_extract_vendor_name(text), "Acme Corp")

    def test_vendor_keyword(self):
        text = "Vendor: WidgetWorks LLC\nDate: 2024-01-01"
        self.assertEqual(_extract_vendor_name(text), "WidgetWorks LLC")

    def test_supplier_keyword(self):
        text = "Supplier: Global Parts Inc.\nRef: 999"
        self.assertEqual(_extract_vendor_name(text), "Global Parts Inc")

    def test_first_line_fallback(self):
        """When no keyword is found, use the first meaningful line."""
        text = "TechnoSupply Ltd\n123 Main St\nInvoice #456"
        self.assertEqual(_extract_vendor_name(text), "TechnoSupply Ltd")

    def test_skips_invoice_header(self):
        """Lines starting with 'Invoice' should be skipped as vendor name."""
        text = "Invoice #100\nBestVendor Inc\nDate: 2024-01-01"
        self.assertEqual(_extract_vendor_name(text), "BestVendor Inc")

    def test_empty_text(self):
        self.assertIsNone(_extract_vendor_name(""))


# ── Total / currency ────────────────────────────────────────

class TestExtractTotal(unittest.TestCase):

    def test_total_with_dollar(self):
        text = "Subtotal: $800.00\nTax: $64.00\nTotal: $864.00"
        amount, currency = _extract_total(text)
        self.assertEqual(amount, 864.00)
        self.assertEqual(currency, "USD")

    def test_grand_total(self):
        text = "Grand Total: €1,250.00"
        amount, currency = _extract_total(text)
        self.assertEqual(amount, 1250.00)
        self.assertEqual(currency, "EUR")

    def test_amount_due(self):
        text = "Amount Due: £350.50"
        amount, currency = _extract_total(text)
        self.assertEqual(amount, 350.50)
        self.assertEqual(currency, "GBP")

    def test_total_no_symbol_defaults_usd(self):
        text = "Total: 999.99"
        amount, currency = _extract_total(text)
        self.assertEqual(amount, 999.99)
        self.assertEqual(currency, "USD")

    def test_total_with_iso_code(self):
        text = "Total Amount CAD 1500.00"
        amount, currency = _extract_total(text)
        self.assertEqual(amount, 1500.00)
        self.assertEqual(currency, "CAD")

    def test_total_with_commas(self):
        text = "Total: $12,345.67"
        amount, currency = _extract_total(text)
        self.assertEqual(amount, 12345.67)
        self.assertEqual(currency, "USD")

    def test_no_total_found(self):
        amount, currency = _extract_total("Nothing relevant here")
        self.assertIsNone(amount)
        self.assertIsNone(currency)


# ── Line items ───────────────────────────────────────────────

class TestExtractLineItems(unittest.TestCase):

    def test_four_column_qty_first(self):
        text = (
            "Item          Qty   Price    Total\n"
            "2    Widget Pro       $25.00   $50.00\n"
            "1    Gadget Lite      $15.00   $15.00\n"
        )
        items = _extract_line_items(text)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["description"], "Widget Pro")
        self.assertEqual(items[0]["quantity"], 2.0)
        self.assertEqual(items[0]["unit_price"], 25.00)
        self.assertEqual(items[0]["line_total"], 50.00)

    def test_four_column_desc_first(self):
        text = (
            "Description      Qty  Unit Price  Total\n"
            "Consulting Hour  10   150.00      1500.00\n"
        )
        items = _extract_line_items(text)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["description"], "Consulting Hour")
        self.assertEqual(items[0]["quantity"], 10.0)
        self.assertEqual(items[0]["unit_price"], 150.00)
        self.assertEqual(items[0]["line_total"], 1500.00)

    def test_two_column_format(self):
        text = (
            "Web Design Services   $2,500.00\n"
            "Logo Design           $500.00\n"
        )
        items = _extract_line_items(text)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["description"], "Web Design Services")
        self.assertEqual(items[0]["line_total"], 2500.00)
        self.assertIsNone(items[0]["quantity"])

    def test_skips_total_lines(self):
        """Lines with 'Total' / 'Subtotal' in description should be skipped."""
        text = (
            "Cleaning Service   $100.00\n"
            "Subtotal           $100.00\n"
            "Total              $100.00\n"
        )
        items = _extract_line_items(text)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["description"], "Cleaning Service")

    def test_no_line_items(self):
        items = _extract_line_items("Just some paragraph text with no table.")
        self.assertEqual(items, [])


# ── Full integration: parse_invoice_text ─────────────────────

class TestParseInvoiceText(unittest.TestCase):

    SAMPLE_INVOICE = (
        "Acme Corporation\n"
        "123 Business Ave, Suite 100\n"
        "New York, NY 10001\n"
        "\n"
        "Invoice #INV-2024-0042\n"
        "Invoice Date: 01/15/2024\n"
        "\n"
        "Bill To:\n"
        "John Smith\n"
        "456 Client St\n"
        "\n"
        "Description          Qty  Unit Price  Total\n"
        "Consulting Hour      10   150.00      1500.00\n"
        "Travel Expense       1    250.00      250.00\n"
        "\n"
        "Subtotal: $1,750.00\n"
        "Tax (8%): $140.00\n"
        "Total: $1,890.00\n"
    )

    def test_high_confidence_parse(self):
        result = parse_invoice_text(self.SAMPLE_INVOICE)
        self.assertEqual(result["invoice_number"], "INV-2024-0042")
        self.assertEqual(result["invoice_date"], "2024-01-15")
        self.assertEqual(result["vendor_name"], "Acme Corporation")
        self.assertEqual(result["total_amount"], 1890.00)
        self.assertEqual(result["currency"], "USD")
        self.assertEqual(result["confidence"], "high")
        self.assertGreaterEqual(len(result["line_items"]), 1)

    def test_medium_confidence(self):
        """Only invoice number and date → 2 fields → medium."""
        text = "Invoice #55\nDate: 2024-06-01"
        result = parse_invoice_text(text)
        self.assertEqual(result["confidence"], "medium")

    def test_low_confidence(self):
        """One or zero fields extracted → low."""
        text = "Random text with nothing useful."
        result = parse_invoice_text(text)
        self.assertEqual(result["confidence"], "low")

    def test_empty_text(self):
        result = parse_invoice_text("")
        self.assertIsNone(result["invoice_number"])
        self.assertIsNone(result["invoice_date"])
        self.assertIsNone(result["total_amount"])
        self.assertEqual(result["line_items"], [])
        self.assertEqual(result["confidence"], "low")

    def test_all_fields_present_in_result(self):
        result = parse_invoice_text("anything")
        expected_keys = {
            "invoice_number", "invoice_date", "vendor_name",
            "total_amount", "currency", "line_items", "confidence",
        }
        self.assertEqual(set(result.keys()), expected_keys)

    def test_partial_fields_still_returns(self):
        """Even with only a total, parsing should succeed."""
        text = "Some company\nTotal: $42.00"
        result = parse_invoice_text(text)
        self.assertEqual(result["total_amount"], 42.00)
        self.assertIsNotNone(result["vendor_name"])


if __name__ == "__main__":
    unittest.main()
