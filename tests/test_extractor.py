"""
Tests for the LLM invoice extractor module.

Covers:
  - extract_invoice_data in mock mode (high confidence, low confidence null total, low confidence mismatch)
  - process_and_validate coordination
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config
from src.extractor import extract_invoice_data, process_and_validate


class TestExtractor(unittest.TestCase):

    def setUp(self):
        self.original_mock = config.MOCK_EXTRACTOR

    def tearDown(self):
        config.MOCK_EXTRACTOR = self.original_mock

    @patch("src.extractor.random.random")
    def test_mock_extractor_high_confidence(self, mock_random):
        mock_random.return_value = 0.5  # < 0.90
        config.MOCK_EXTRACTOR = True

        data = extract_invoice_data("dummy ocr text")
        self.assertEqual(data["confidence"], "high")
        self.assertIsNotNone(data["total_amount"])
        self.assertIn("line_items", data)

    @patch("src.extractor.random.random")
    def test_mock_extractor_low_confidence_null_total(self, mock_random):
        mock_random.return_value = 0.92  # between 0.90 and 0.95
        config.MOCK_EXTRACTOR = True

        data = extract_invoice_data("dummy ocr text")
        self.assertEqual(data["confidence"], "low")
        self.assertIsNone(data["total_amount"])

    @patch("src.extractor.random.random")
    def test_mock_extractor_low_confidence_mismatch(self, mock_random):
        mock_random.return_value = 0.97  # >= 0.95
        config.MOCK_EXTRACTOR = True

        data = extract_invoice_data("dummy ocr text")
        self.assertEqual(data["confidence"], "low")
        self.assertIsNotNone(data["total_amount"])

    def test_process_and_validate_valid(self):
        config.MOCK_EXTRACTOR = True
        # Force high confidence mock branch
        with patch("src.extractor.random.random", return_value=0.1):
            result = process_and_validate("text", "test.png", "hash123")
            self.assertTrue(result["invoice_number"].startswith("MOCK"))
            self.assertEqual(result["status"], "needs_review")  # Default schema status unless overridden

    def test_process_and_validate_invalid(self):
        config.MOCK_EXTRACTOR = True
        # Force low confidence / mismatch mock branch that fails validation
        with patch("src.extractor.random.random", return_value=0.97):
            result = process_and_validate("text", "test.png", "hash123")
            self.assertEqual(result.get("status"), "needs_review")


if __name__ == "__main__":
    unittest.main()
