"""
Pipeline integrity tests.

These tests run against a SEPARATE database called 'invoice_test'
and use temporary folders — they never touch the real database or
the real input_invoices directory.

NOTE: test_status_summation runs the real OCR pipeline (Tesseract)
with a mocked extractor, so Tesseract must be installed.
"""

import pytest
import mysql.connector
from pathlib import Path
from PIL import Image

from src import config, db, intake, ocr, extractor, storage

# ── Constants ────────────────────────────────────────────────
TEST_DB_NAME = "invoice_test"

# ── Session-scoped: create the test database once ────────────
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


# ── Per-test: truncate all rows so every test starts clean ───
@pytest.fixture(autouse=True)
def clean_tables():
    """Truncate every table before each test (FK-safe)."""
    conn = db.get_connection()
    cursor = conn.cursor()

    # ── SAFETY GUARD ──────────────────────────────────────────
    cursor.execute("SELECT DATABASE()")
    current_db = cursor.fetchone()[0]
    if current_db != TEST_DB_NAME:
        cursor.close()
        conn.close()
        pytest.exit(
            f"Refusing to run: connected to '{current_db}', not '{TEST_DB_NAME}'"
        )
    # ──────────────────────────────────────────────────────────

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


# ── Per-test: redirect intake to a temp folder ───────────────
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

    monkeypatch.setattr(config, "INPUT_DIR", input_dir)
    monkeypatch.setattr(config, "FAILED_DIR", failed_dir)
    monkeypatch.setattr(config, "PROCESSED_DIR", processed_dir)
    monkeypatch.setattr(config, "OCR_OUTPUT_DIR", ocr_output_dir)
    yield


# ━━━━━━━━━━━━━━━━━━  TESTS  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# (a) Two files with the same content → only ONE non-duplicate row
def test_duplicate_rejection():
    """
    When a file's hash already exists in invoice_documents, intake
    must NOT insert a second 'success' row.  The duplicate should
    be recorded in duplicate_files.
    """
    img = Image.new("RGB", (100, 100), color="red")
    first_file = config.INPUT_DIR / "original.png"
    img.save(first_file)
    first_hash = intake.compute_file_hash(first_file)

    storage.store_invoice(
        file_name="original.png",
        file_hash=first_hash,
        ocr_text_path="ocr_output/original.txt",
        parsed={
            "invoice_number": "INV-001",
            "invoice_date": "2026-09-30",
            "vendor_name": "Vendor A",
            "total_amount": 100.0,
            "currency": "INR",
            "line_items": [],
            "confidence": "high",
        },
    )
    first_file.unlink()

    duplicate_file = config.INPUT_DIR / "copy.png"
    img.save(duplicate_file)

    results = intake.scan_input_folder()

    dup_results = [r for r in results if r["status"] == "duplicate_skipped"]
    assert len(dup_results) == 1, f"Expected 1 duplicate_skipped result, got {len(dup_results)}"

    conn = db.get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT COUNT(*) FROM invoice_documents WHERE file_hash = %s",
        (first_hash,),
    )
    doc_count = cursor.fetchone()[0]
    assert doc_count == 1, f"Expected 1 invoice document row, got {doc_count}"

    cursor.execute(
        "SELECT COUNT(*) FROM duplicate_files WHERE file_hash = %s",
        (first_hash,),
    )
    dup_count = cursor.fetchone()[0]
    assert dup_count == 1, f"Expected 1 duplicate_files row, got {dup_count}"

    cursor.close()
    conn.close()


# (b) Mixed batch: the real pipeline path, DB status counts
def test_pipeline_mixed_batch(monkeypatch):
    """
    Run a batch of:
    - one valid image
    - one corrupt file
    - one copy of the valid image
    Assert: success=1, failed=1, duplicates=1
    """
    from src.main import process_file

    monkeypatch.setattr(config, "MOCK_EXTRACTOR", True)
    monkeypatch.setattr(extractor, "extract_invoice_data", lambda text: {
        "invoice_number": "MOCK-1234",
        "invoice_date": "2026-09-30",
        "vendor_name": "Mock Stationery",
        "total_amount": 2000.00,
        "currency": "INR",
        "line_items": [
            {
                "description": "Mock Item",
                "quantity": 50.0,
                "unit_price": 40.0,
                "line_total": 2000.0,
            }
        ],
        "confidence": "high",
    })

    valid_img = Image.new("RGB", (200, 200), color="blue")
    valid_path = config.INPUT_DIR / "good.png"
    valid_img.save(valid_path)

    (config.INPUT_DIR / "corrupt.png").write_text("not an image")

    dup_path = config.INPUT_DIR / "dup_good.png"
    valid_img.save(dup_path)

    results = intake.scan_input_folder()

    for result in results:
        if result["status"] == "ready":
             process_file(result)

    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status, COUNT(*) FROM invoice_documents GROUP BY status")
    rows = cursor.fetchall()
    counts = {r[0]: r[1] for r in rows}

    cursor.execute("SELECT COUNT(*) FROM duplicate_files")
    dup_count = cursor.fetchone()[0]
    cursor.close()
    conn.close()

    assert counts.get("success", 0) == 1
    assert counts.get("failed", 0) == 1
    assert dup_count == 1
    assert counts.get("success", 0) + counts.get("needs_review", 0) + counts.get("failed", 0) + dup_count == 3


# (c) needs_review / failed rows always have a reason
def test_reasons_present():
    """Every row with status 'needs_review' or 'failed' must have a reason."""
    storage.store_invoice(
        file_name="review_me.png",
        file_hash="a" * 64,
        ocr_text_path=None,
        parsed={"confidence": "low"},
        review_reason="Missing all fields",
    )

    storage.store_failed_invoice("fail_me.png", "b" * 64, "OCR failure")

    conn = db.get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM invoice_documents WHERE status = 'needs_review' AND (review_reason IS NULL OR review_reason = '')")
    assert cursor.fetchone()[0] == 0

    cursor.execute("SELECT COUNT(*) FROM invoice_documents WHERE status = 'failed' AND (failure_reason IS NULL OR failure_reason = '')")
    assert cursor.fetchone()[0] == 0

    cursor.close()
    conn.close()
