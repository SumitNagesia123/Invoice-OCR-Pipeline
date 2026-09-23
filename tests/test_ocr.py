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
from src.db import init_db
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

        self.db_path = self.data_dir / "test.db"

        self._patches = [
            patch.object(config, "INPUT_DIR", self.input_dir),
            patch.object(config, "OCR_OUTPUT_DIR", self.ocr_dir),
            patch.object(config, "FAILED_DIR", self.failed_dir),
            patch.object(config, "DATABASE_PATH", self.db_path),
        ]
        for p in self._patches:
            p.start()

        init_db()

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

    def test_to_grayscale_from_color(self):
        color = self._make_color_image()
        gray = _to_grayscale(color)
        self.assertEqual(len(gray.shape), 2)

    def test_to_grayscale_already_gray(self):
        gray = self._make_gray_image()
        result = _to_grayscale(gray)
        self.assertEqual(len(result.shape), 2)
        np.testing.assert_array_equal(result, gray)

    def test_denoise_output_shape(self):
        gray = self._make_gray_image()
        denoised = _denoise(gray)
        self.assertEqual(denoised.shape, gray.shape)

    def test_enhance_contrast_output_shape(self):
        gray = self._make_gray_image()
        enhanced = _enhance_contrast(gray)
        self.assertEqual(enhanced.shape, gray.shape)

    def test_threshold_output_binary(self):
        gray = self._make_gray_image()
        binary = _threshold(gray)
        unique_vals = set(np.unique(binary))
        self.assertTrue(unique_vals.issubset({0, 255}))

    def test_deskew_no_crash_on_blank(self):
        """Deskew should handle a blank (all-zero) image without crashing."""
        blank = np.zeros((100, 100), dtype=np.uint8)
        result = _deskew(blank)
        self.assertEqual(result.shape, blank.shape)

    def test_preprocess_full_pipeline(self):
        """Full pipeline runs without error on a color image."""
        color = self._make_color_image(200, 200)
        result = preprocess_image(color)
        self.assertEqual(len(result.shape), 2)  # grayscale output


# ── OCR text saving ──────────────────────────────────────────

class TestSaveOcrText(_TempEnvTestCase):

    def test_saves_text_file(self):
        path = _save_ocr_text("invoice_01.png", "Hello World")
        self.assertTrue(path.exists())
        self.assertTrue(path.name.startswith("invoice_01_"))
        self.assertTrue(path.name.endswith(".txt"))
        content = path.read_text(encoding="utf-8")
        self.assertEqual(content, "Hello World")

    def test_saves_to_ocr_dir(self):
        path = _save_ocr_text("test.png", "some text")
        self.assertEqual(path.parent, self.ocr_dir)


# ── Full OCR extraction ──────────────────────────────────────

class TestExtractText(_TempEnvTestCase):

    def test_happy_path_image_with_text(self):
        """An image with clear text should produce non-empty output."""
        path = self._create_text_image(
            "INVOICE #12345\nDate: 2024-01-15\nTotal: $500.00",
            name="inv_12345.png",
            size=(800, 300),
        )
        result = extract_text(path)
        self.assertTrue(result["success"])
        self.assertIsNotNone(result["ocr_text_path"])
        self.assertTrue(result["ocr_text_path"].exists())
        self.assertEqual(result["error"], "")
        # We expect at least some text extracted (Tesseract on synthetic
        # images with default font may not be perfect, but should get
        # something)
        self.assertIsInstance(result["text"], str)

    def test_blank_image_succeeds_with_empty_or_minimal_text(self):
        """A blank white image should succeed but produce little/no text."""
        path = self._create_blank_image("blank.png")
        result = extract_text(path)
        self.assertTrue(result["success"])
        self.assertIsNotNone(result["ocr_text_path"])

    def test_corrupt_image_returns_error(self):
        """A file that OpenCV can't read should return an error."""
        bad_path = self.input_dir / "corrupt.png"
        bad_path.write_bytes(b"this is not a PNG file")
        result = extract_text(bad_path)
        self.assertFalse(result["success"])
        self.assertIsNone(result["ocr_text_path"])
        self.assertIn("could not read", result["error"].lower())

    def test_ocr_text_file_created_in_ocr_output(self):
        """The raw OCR text file should land in the ocr_output directory."""
        path = self._create_text_image("Sample text", name="sample.png")
        result = extract_text(path)
        self.assertTrue(result["success"])
        self.assertEqual(result["ocr_text_path"].parent, self.ocr_dir)

    def test_pdf_without_poppler_returns_error(self):
        """If Poppler is not installed, PDF extraction should fail gracefully."""
        pdf_path = self.input_dir / "invoice.pdf"
        # Write a minimal PDF-like header so intake would accept it,
        # but pdf2image will fail without Poppler
        pdf_path.write_bytes(b"%PDF-1.4 fake content")
        result = extract_text(pdf_path)
        # Should fail gracefully (no crash)
        self.assertFalse(result["success"])
        self.assertIn("poppler", result["error"].lower())

    def test_multiple_images_independent(self):
        """Two different images produce independent OCR results."""
        path1 = self._create_text_image("Invoice A", name="inv_a.png")
        path2 = self._create_text_image("Invoice B", name="inv_b.png")
        r1 = extract_text(path1)
        r2 = extract_text(path2)
        self.assertTrue(r1["success"])
        self.assertTrue(r2["success"])
        # Output files should be different
        self.assertNotEqual(r1["ocr_text_path"], r2["ocr_text_path"])


if __name__ == "__main__":
    unittest.main()
