"""
Pipeline integrity tests.

These tests run against a SEPARATE database called 'invoice_test'
and use temporary folders — they never touch the real database or
the real input_invoices directory.
"""

import pytest
import shutil
import mysql.connector
from pathlib import Path
from PIL import Image

from src import config, db, intake, ocr, extractor, storage, main

TEST_DB_NAME = "invoice_test"


@pytest.fixture(scope="session", autouse=True)
def create_test_database():
    """Create the invoice_test database and run migrations."""
    conn = mysql.connector.connect(
        host=config.DB_HOST,
        port=config.DB_PORT,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
    )
    cursor = conn.cursor()
    cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{TEST_DB_NAME}`")
    conn.commit()
    cursor.close()
    conn.close()

    config.DB_NAME = TEST_DB_NAME

    # ── SAFETY GUARD ──────────────────────────────────────────
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT DATABASE()")
    current_db = cursor.fetchone()[0]
    cursor.close()
    conn.close()
    if current_db != TEST_DB_NAME:
        pytest.exit(
            f"Refusing to run: connected to '{current_db}', not '{TEST_DB_NAME}'"
        )
    # ──────────────────────────────────────────────────────────

    db.init_db()
    yield


@pytest.fixture(autouse=True)
def clean_tables():
    """Truncate every table before each test (FK-safe)."""
    conn = db.get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT DATABASE()")
    current_db = cursor.fetchone()[0]
    if current_db != TEST_DB_NAME:
        cursor.close()
        conn.close()
        pytest.exit(
            f"Refusing to run: connected to '{current_db}', not '{TEST_DB_NAME}'"
        )

    cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
    cursor.execute("TRUNCATE TABLE invoice_line_items")
    cursor.execute("TRUNCATE TABLE invoice_documents")
    cursor.execute("TRUNCATE TABLE processing_logs")
    cursor.execute("TRUNCATE TABLE duplicate_files")
    cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
    conn.commit()
    cursor.close()
    conn.close()
    yield


@pytest.fixture(autouse=True)
def use_tmp_dirs(tmp_path, monkeypatch):
    """Point config dirs at tmp_path so real folders are never touched."""
    input_dir = tmp_path / "input_invoices"
    failed_dir = tmp_path / "failed_invoices"
    processed_dir = tmp_path / "processed_invoices"
    ocr_output_dir = tmp_path / "ocr_output"
    input_dir.mkdir()
    failed_dir.mkdir()
    processed_dir.mkdir()
    ocr_output_dir.mkdir()

    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(config, "INPUT_DIR", input_dir)
    monkeypatch.setattr(config, "FAILED_DIR", failed_dir)
    monkeypatch.setattr(config, "PROCESSED_DIR", processed_dir)
    monkeypatch.setattr(config, "OCR_OUTPUT_DIR", ocr_output_dir)
    yield


@pytest.fixture(autouse=True)
def mock_extractor_and_ocr(monkeypatch):
    """Provide deterministic mock extractor and mock ocr."""
    monkeypatch.setattr(config, "MOCK_EXTRACTOR", True)
    monkeypatch.setattr(extractor, "extract_invoice_data", lambda text: {
        "invoice_number": "INV-MOCK-001",
        "invoice_date": "2026-10-01",
        "vendor_name": "Mock Vendor",
        "total_amount": 500.00,
        "currency": "USD",
        "line_items": [
            {
                "description": "Widget",
                "quantity": 2,
                "unit_price": 250.00,
                "line_total": 500.00,
            }
        ],
        "confidence": "high",
    })
    monkeypatch.setattr(ocr, "extract_text", lambda path: {
        "success": True,
        "text": "Invoice # INV-MOCK-001 Date: 2026-10-01 Total: $500.00",
        "ocr_text_path": config.OCR_OUTPUT_DIR / f"{path.stem}_ocr.txt",
        "error": "",
    })


def test_in_batch_duplicate():
    """In-batch duplicate files are detected and skipped."""
    img = Image.new("RGB", (100, 100), color="blue")
    img.save(config.INPUT_DIR / "file1.png")
    img.save(config.INPUT_DIR / "file2.png")

    results = intake.scan_input_folder()
    statuses = [r["status"] for r in results]
    assert "ready" in statuses
    assert "duplicate_skipped" in statuses


def test_pipeline_mixed_batch():
    """Mixed batch (valid, corrupt, duplicate) yields correct status counts."""
    valid_img = Image.new("RGB", (100, 100), color="green")
    valid_path = config.INPUT_DIR / "invoice_valid.png"
    valid_img.save(valid_path)

    corrupt_path = config.INPUT_DIR / "invoice_corrupt.png"
    corrupt_path.write_text("random garbage content")

    dup_path = config.INPUT_DIR / "invoice_copy.png"
    valid_img.save(dup_path)

    results = intake.scan_input_folder()
    for r in results:
        if r["status"] == "ready":
            main.process_file(r)

    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status, COUNT(*) FROM invoice_documents GROUP BY status")
    counts = {row[0]: row[1] for row in cursor.fetchall()}
    cursor.execute("SELECT COUNT(*) FROM duplicate_files")
    dup_count = cursor.fetchone()[0]
    cursor.close()
    conn.close()

    assert counts.get("success", 0) == 1
    assert counts.get("failed", 0) == 1
    assert dup_count == 1


def test_reasons_present():
    """Needs review and failed rows always have a reason recorded."""
    storage.store_invoice(
        file_name="review.png",
        file_hash="1" * 64,
        ocr_text_path=None,
        parsed={"confidence": "low"},
        review_reason="Low confidence score",
    )
    storage.store_failed_invoice("fail.png", "2" * 64, "Corrupt file format")

    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT review_reason FROM invoice_documents WHERE status = 'needs_review'")
    assert cursor.fetchone()[0] is not None
    cursor.execute("SELECT failure_reason FROM invoice_documents WHERE status = 'failed'")
    assert cursor.fetchone()[0] is not None
    cursor.close()
    conn.close()


def test_no_absolute_ocr_text_path():
    """OCR text path stored in database is relative, not absolute."""
    valid_img = Image.new("RGB", (100, 100), color="white")
    path = config.INPUT_DIR / "inv.png"
    valid_img.save(path)

    results = intake.scan_input_folder()
    ready_item = next(r for r in results if r["status"] == "ready")
    main.process_file(ready_item)

    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT ocr_text_path FROM invoice_documents WHERE file_name = 'inv.png'")
    ocr_path = cursor.fetchone()[0]
    cursor.close()
    conn.close()

    assert ocr_path is not None
    assert not Path(ocr_path).is_absolute()


def test_corrupt_file_stored_with_real_hash():
    """Corrupt file is stored with its real SHA-256 hash and failed status."""
    corrupt_path = config.INPUT_DIR / "corrupt_hash.png"
    corrupt_content = b"some invalid image bytes"
    corrupt_path.write_bytes(corrupt_content)

    expected_hash = intake.compute_file_hash(corrupt_path)

    results = intake.scan_input_folder()
    assert len(results) == 1
    assert results[0]["status"] == "failed"
    assert results[0]["file_hash"] == expected_hash

    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT file_hash, status FROM invoice_documents WHERE file_name = 'corrupt_hash.png'")
    row = cursor.fetchone()
    cursor.close()
    conn.close()

    assert row[0] == expected_hash
    assert row[1] == "failed"


def test_empty_file_stored_with_null_hash():
    """Empty (0-byte) file is stored with NULL hash and failed status."""
    empty_path = config.INPUT_DIR / "empty.png"
    empty_path.write_bytes(b"")

    results = intake.scan_input_folder()
    assert len(results) == 1
    assert results[0]["status"] == "failed"
    assert results[0]["file_hash"] is None

    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT file_hash, status FROM invoice_documents WHERE file_name = 'empty.png'")
    row = cursor.fetchone()
    cursor.close()
    conn.close()

    assert row[0] is None
    assert row[1] == "failed"


def test_failed_file_retry_succeeds():
    """A file that failed previously can be retried and succeed."""
    corrupt_path = config.INPUT_DIR / "retry_invoice.png"
    corrupt_path.write_bytes(b"bad")
    intake.scan_input_folder()

    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM invoice_documents WHERE file_name = 'retry_invoice.png'")
    assert cursor.fetchone()[0] == "failed"
    cursor.close()
    conn.close()

    failed_file = config.FAILED_DIR / "retry_invoice.png"
    if failed_file.exists():
        shutil.move(str(failed_file), str(corrupt_path))
    valid_img = Image.new("RGB", (100, 100), color="yellow")
    valid_img.save(corrupt_path)

    results = intake.scan_input_folder()
    ready_item = next(r for r in results if r["status"] == "ready")
    main.process_file(ready_item)

    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM invoice_documents WHERE file_name = 'retry_invoice.png'")
    assert cursor.fetchone()[0] == "success"
    cursor.close()
    conn.close()


def test_copy_of_failed_file_retried_not_skipped():
    """A copy or retry of a failed file is not skipped as duplicate."""
    path1 = config.INPUT_DIR / "fail_doc.png"
    path1.write_bytes(b"bad content")
    intake.scan_input_folder()

    path2 = config.INPUT_DIR / "fail_doc_copy.png"
    path2.write_bytes(b"bad content")

    results = intake.scan_input_folder()
    statuses = [r["status"] for r in results]
    assert "duplicate_skipped" not in statuses
