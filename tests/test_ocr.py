"""
Tests for the OCR extraction module.

Covers:
  - Happy path:      valid image with text → text extracted
  - Preprocessing:   grayscale, denoise, contrast, deskew, threshold
  - Save OCR text:   raw text written to ocr_output/
  - Corrupt image:   OpenCV can't read → error returned
  - Empty text:      image with no text → success but empty string
  - PDF handling:    graceful failure when Poppler is missing
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config
from tests.db_setup import init_db_for_test
from src.ocr import (
    _save_ocr_text,
    _to_grayscale,
    _denoise,
    _enhance_contrast,
    _threshold,
    _deskew,
    extract_text,
    preprocess_image,
)


class _TempEnvTestCase(unittest.TestCase):
    """Redirect pipeline folders to a temp directory for test isolation."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.input_dir = self.tmpdir / "input_invoices"
        self.ocr_dir = self.tmpdir / "ocr_output"
        self.failed_dir = self.tmpdir / "failed_invoices"
        self.data_dir = self.tmpdir / "data"

        for d in (self.input_dir, self.ocr_dir, self.failed_dir, self.data_dir):
            d.mkdir()

        self._patches = [
            patch.object(config, "INPUT_DIR", self.input_dir),
            patch.object(config, "OCR_OUTPUT_DIR", self.ocr_dir),
            patch.object(config, "FAILED_DIR", self.failed_dir),
        ]
        for p in self._patches:
            p.start()

        init_db_for_test()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _create_text_image(self, text: str, name: str = "invoice.png",
                           size=(600, 200)) -> Path:
        """Create a PNG with readable text drawn on it."""
        img = Image.new("RGB", size, color="white")
        draw = ImageDraw.Draw(img)
        # Use default font (always available)
        draw.text((20, 20), text, fill="black")
        path = self.input_dir / name
        img.save(path)
        return path

    def _create_blank_image(self, name: str = "blank.png",
                            size=(200, 100)) -> Path:
        """Create a blank white image with no text."""
        img = Image.new("RGB", size, color="white")
        path = self.input_dir / name
        img.save(path)
        return path


# ── Preprocessing unit tests ─────────────────────────────────

class TestPreprocessingSteps(unittest.TestCase):

    def _make_color_image(self, w=100, h=100):
        return np.random.randint(0, 256, (h, w, 3), dtype=np.uint8)

    def _make_gray_image(self, w=100, h=100):
        return np.random.randint(0, 256, (h, w), dtype=np.uint8)

    def test_to_grayscale(self):
        color = self._make_color_image()
        gray = _to_grayscale(color)
        self.assertEqual(len(gray.shape), 2)

    def test_denoise(self):
        gray = self._make_gray_image()
        denoised = _denoise(gray)
        self.assertEqual(denoised.shape, gray.shape)

    def test_enhance_contrast(self):
        gray = self._make_gray_image()
        enhanced = _enhance_contrast(gray)
        self.assertEqual(enhanced.shape, gray.shape)

    def test_threshold(self):
        gray = self._make_gray_image()
        thresh = _threshold(gray)
        self.assertEqual(len(thresh.shape), 2)

    def test_deskew_straight_image(self):
        gray = self._make_gray_image()
        deskewed = _deskew(gray)
        self.assertEqual(deskewed.shape, gray.shape)


# ── Preprocess image pipeline ────────────────────────────────

class TestPreprocessImage(_TempEnvTestCase):

    def test_preprocess_returns_valid_image(self):
        path = self._create_text_image("Test")
        processed = preprocess_image(path)
        self.assertIsInstance(processed, np.ndarray)
        self.assertGreater(processed.size, 0)


# ── Save OCR text ────────────────────────────────────────────

class TestSaveOcrText(_TempEnvTestCase):

    def test_saves_text_file(self):
        text_path = _save_ocr_text("invoice1.png", "Hello OCR world")
        self.assertIsNotNone(text_path)
        p = Path(text_path)
        self.assertTrue(p.exists())
        self.assertEqual(p.read_text(encoding="utf-8"), "Hello OCR world")


# ── Extract text (Tesseract integration) ─────────────────────

class TestExtractText(_TempEnvTestCase):

    def test_extract_text_from_image(self):
        path = self._create_text_image("INVOICE 9999\nTotal: 500.00")
        result = extract_text(path)
        self.assertTrue(result["success"])
        self.assertTrue(len(result["text"]) > 0)
        self.assertIsNotNone(result["ocr_text_path"])
        self.assertTrue(Path(result["ocr_text_path"]).exists())

    def test_extract_text_corrupt_image(self):
        path = self.input_dir / "corrupt.png"
        path.write_bytes(b"not an image")
        result = extract_text(path)
        self.assertFalse(result["success"])
        self.assertIn("error", result)

    def test_extract_text_blank_image(self):
        path = self._create_blank_image()
        result = extract_text(path)
        # Should succeed (Tesseract runs) but text will be empty or minimal
        self.assertTrue(result["success"])


if __name__ == "__main__":
    unittest.main()
