"""
Tests for the file-intake module.

Covers:
  - Happy path:     valid image picked up, marked 'ready'
  - Invalid input:  corrupt / empty / unsupported files → failed
  - Empty state:    empty input folder → no results, no crash
  - Duplicate:      same file hash → skipped
  - Filename sanitization
"""

import os
import random
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config  # noqa: E402
from src.db import init_db, get_connection
from src.intake import (
    compute_file_hash,
    is_file_valid,
    move_to_failed,
    sanitize_filename,
    scan_input_folder,
)


class _TempEnvTestCase(unittest.TestCase):
    """
    Base class that redirects all pipeline folders to a temp directory
    so tests don't touch the real project folders or database.
    """

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.input_dir = self.tmpdir / "input_invoices"
        self.processed_dir = self.tmpdir / "processed_invoices"
        self.failed_dir = self.tmpdir / "failed_invoices"
        self.ocr_dir = self.tmpdir / "ocr_output"
        self.data_dir = self.tmpdir / "data"

        for d in (self.input_dir, self.processed_dir,
                  self.failed_dir, self.ocr_dir, self.data_dir):
            d.mkdir()

        self.db_path = self.data_dir / "test.db"

        # Patch config paths so src modules read them at call time
        self._patches = [
            patch.object(config, "INPUT_DIR", self.input_dir),
            patch.object(config, "PROCESSED_DIR", self.processed_dir),
            patch.object(config, "FAILED_DIR", self.failed_dir),
            patch.object(config, "OCR_OUTPUT_DIR", self.ocr_dir),
            patch.object(config, "DATABASE_PATH", self.db_path),
        ]
        for p in self._patches:
            p.start()

        # Initialize DB in temp location
        init_db()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ── Helpers ───────────────────────────────────────────────

    def _create_test_image(self, name: str = "invoice.png",
                           size=(200, 100)) -> Path:
        """Create a valid PNG with unique pixel content."""
        path = self.input_dir / name
        img = Image.new("RGB", size, color=(
            random.randint(0, 255),
            random.randint(0, 255),
            random.randint(0, 255),
        ))
        img.save(path)
        return path

    def _create_identical_image(self, name: str, source: Path) -> Path:
        """Copy an existing image to create a true duplicate."""
        dest = self.input_dir / name
        shutil.copy2(str(source), str(dest))
        return dest

    def _create_empty_file(self, name: str) -> Path:
        path = self.input_dir / name
        path.write_bytes(b"")
        return path

    def _create_corrupt_file(self, name: str) -> Path:
        path = self.input_dir / name
        path.write_bytes(b"not a real image at all")
        return path


# ── Test cases ────────────────────────────────────────────────

class TestSanitizeFilename(unittest.TestCase):

    def test_safe_name_unchanged(self):
        self.assertEqual(sanitize_filename("invoice_01.png"), "invoice_01.png")

    def test_path_traversal_stripped(self):
        result = sanitize_filename("../../etc/passwd")
        self.assertNotIn("..", result)
        self.assertNotIn("/", result)

    def test_backslash_replaced(self):
        result = sanitize_filename("folder\\file.png")
        self.assertNotIn("\\", result)

    def test_leading_dots_removed(self):
        result = sanitize_filename(".hidden_file.png")
        self.assertFalse(result.startswith("."))

    def test_null_bytes_removed(self):
        result = sanitize_filename("file\0name.png")
        self.assertNotIn("\0", result)


class TestComputeFileHash(unittest.TestCase):

    def test_deterministic(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
            f.write(b"hello world")
            path = Path(f.name)
        try:
            h1 = compute_file_hash(path)
            h2 = compute_file_hash(path)
            self.assertEqual(h1, h2)
            self.assertEqual(len(h1), 64)  # SHA-256 hex
        finally:
            path.unlink()

    def test_different_content_different_hash(self):
        paths = []
        for content in (b"aaa", b"bbb"):
            f = tempfile.NamedTemporaryFile(delete=False, suffix=".txt")
            f.write(content)
            f.close()
            paths.append(Path(f.name))
        try:
            self.assertNotEqual(
                compute_file_hash(paths[0]),
                compute_file_hash(paths[1]),
            )
        finally:
            for p in paths:
                p.unlink()


class TestIsFileValid(_TempEnvTestCase):

    def test_valid_png(self):
        path = self._create_test_image("ok.png")
        ok, reason = is_file_valid(path)
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_empty_file(self):
        path = self._create_empty_file("empty.png")
        ok, reason = is_file_valid(path)
        self.assertFalse(ok)
        self.assertIn("empty", reason.lower())

    def test_corrupt_image(self):
        path = self._create_corrupt_file("corrupt.png")
        ok, reason = is_file_valid(path)
        self.assertFalse(ok)
        self.assertIn("not a readable image", reason.lower())

    def test_unsupported_extension(self):
        path = self.input_dir / "readme.txt"
        path.write_text("hello")
        ok, reason = is_file_valid(path)
        self.assertFalse(ok)
        self.assertIn("unsupported", reason.lower())


class TestScanInputFolder(_TempEnvTestCase):

    def test_happy_path(self):
        self._create_test_image("invoice_001.png")
        results = scan_input_folder()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "ready")
        self.assertEqual(results[0]["file_name"], "invoice_001.png")
        self.assertIsNotNone(results[0]["file_hash"])

    def test_empty_folder(self):
        results = scan_input_folder()
        self.assertEqual(results, [])

    def test_invalid_file_routed_to_failed(self):
        self._create_corrupt_file("bad.png")
        results = scan_input_folder()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "failed")
        # File should have been moved to failed_invoices
        self.assertFalse((self.input_dir / "bad.png").exists())
        failed_files = list(self.failed_dir.iterdir())
        self.assertTrue(any("bad" in f.name for f in failed_files))

    def test_duplicate_hash_skipped(self):
        # Create and scan a valid image
        src_path = self._create_test_image("original.png")
        original_hash = compute_file_hash(src_path)
        results1 = scan_input_folder()
        self.assertEqual(results1[0]["status"], "ready")

        # Insert the first file into the DB so dupe detection works
        conn = get_connection()
        conn.execute(
            "INSERT INTO invoice_documents "
            "(file_name, file_hash, status) VALUES (?, ?, 'processed')",
            ("original.png", original_hash),
        )
        conn.commit()
        conn.close()

        # Remove original from input (it would have been moved by the
        # pipeline in real use), then place an identical copy
        src_path.unlink(missing_ok=True)
        self._create_identical_image("original_copy.png", src_path)

        # Oops — src_path was already deleted.  Create from scratch with
        # the same pixels:  write a deterministic image for this test.
        copy_path = self.input_dir / "original_copy.png"
        if not copy_path.exists():
            # Re-create with same content by writing same hash-able bytes
            img = Image.new("RGB", (200, 100), color=(42, 42, 42))
            img.save(copy_path)

        # We need the copy to have the SAME hash as original.  Easiest way:
        # write the same DB hash and just test that the code catches it.
        # Let's instead re-insert original.png's hash and put a new file
        # whose hash matches.  Simplest: use a fixture.
        # -- re-approach: create two identical files before scan --

    def test_duplicate_hash_skipped(self):
        """A file with the same hash as one already in the DB is skipped."""
        # Create a deterministic image
        img = Image.new("RGB", (10, 10), color=(99, 99, 99))
        first = self.input_dir / "first.png"
        img.save(first)
        file_hash = compute_file_hash(first)

        # Simulate that first.png was already processed
        conn = get_connection()
        conn.execute(
            "INSERT INTO invoice_documents "
            "(file_name, file_hash, status) VALUES (?, ?, 'processed')",
            ("first.png", file_hash),
        )
        conn.commit()
        conn.close()

        # Place an identical file (same content → same hash)
        second = self.input_dir / "second.png"
        img.save(second)
        # Remove first.png so only second.png is scanned
        first.unlink()

        results = scan_input_folder()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "skipped_duplicate")
        self.assertIn("duplicate", results[0]["reason"].lower())
        # The dupe should have been moved out of input
        self.assertFalse((self.input_dir / "second.png").exists())

    def test_unsupported_file_routed_to_failed(self):
        path = self.input_dir / "notes.docx"
        path.write_bytes(b"fake docx content")
        results = scan_input_folder()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "failed")
        self.assertIn("unsupported", results[0]["reason"].lower())

    def test_processing_log_created(self):
        self._create_test_image("logged.png")
        results = scan_input_folder()
        log_id = results[0]["log_id"]
        self.assertIsNotNone(log_id)
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM processing_logs WHERE log_id = ?", (log_id,)
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["file_name"], "logged.png")

    def test_multiple_files_mixed(self):
        """Mix of valid, corrupt, and unsupported files."""
        self._create_test_image("good.png")
        self._create_corrupt_file("bad.jpg")
        (self.input_dir / "doc.txt").write_text("not an invoice")
        results = scan_input_folder()
        statuses = {r["file_name"]: r["status"] for r in results}
        self.assertEqual(statuses["good.png"], "ready")
        self.assertEqual(statuses["bad.jpg"], "failed")
        self.assertEqual(statuses["doc.txt"], "failed")


class TestMoveToFailed(_TempEnvTestCase):

    def test_move_creates_target(self):
        src = self.input_dir / "test.png"
        src.write_bytes(b"data")
        dest = move_to_failed(src, "test reason")
        self.assertTrue(dest.exists())
        self.assertFalse(src.exists())

    def test_name_collision_resolved(self):
        """Two files with the same name don't overwrite each other."""
        # First file
        f1 = self.input_dir / "dup.png"
        f1.write_bytes(b"first")
        move_to_failed(f1, "reason 1")

        # Second file with the same name
        f2 = self.input_dir / "dup.png"
        f2.write_bytes(b"second")
        dest2 = move_to_failed(f2, "reason 2")

        self.assertNotEqual(dest2.name, "dup.png")
        # Both should exist in failed
        failed_files = list(self.failed_dir.iterdir())
        self.assertEqual(len(failed_files), 2)


if __name__ == "__main__":
    unittest.main()
