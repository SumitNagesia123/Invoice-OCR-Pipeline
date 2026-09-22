#!/usr/bin/env python
"""
Initialize (or migrate) the InvoiceOCR database.

Usage:
    python init_db.py
"""

import logging
import sys
from pathlib import Path

# Ensure project root is on sys.path so `src` is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.db import init_db, get_connection, SCHEMA_VERSION

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)


def verify_tables(conn) -> bool:
    """Check that all expected tables and indexes exist."""
    expected_tables = {"schema_version", "invoice_documents",
                       "invoice_line_items", "processing_logs"}
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    actual_tables = {row["name"] for row in rows}

    missing = expected_tables - actual_tables
    if missing:
        print(f"FAIL: Missing tables: {missing}")
        return False

    # Verify indexes
    expected_indexes = {
        "idx_invoice_documents_file_name",
        "idx_invoice_documents_file_hash",
        "idx_invoice_line_items_document_id",
        "idx_processing_logs_file_name",
    }
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    ).fetchall()
    actual_indexes = {row["name"] for row in rows}

    missing_idx = expected_indexes - actual_indexes
    if missing_idx:
        print(f"FAIL: Missing indexes: {missing_idx}")
        return False

    return True


def verify_columns(conn) -> bool:
    """Spot-check that key columns exist with the right names."""
    checks = {
        "invoice_documents": [
            "document_id", "file_name", "file_hash", "invoice_number",
            "invoice_date", "vendor_name", "total_amount", "currency",
            "status", "ocr_text_path", "processed_at",
        ],
        "invoice_line_items": [
            "line_item_id", "document_id", "description",
            "quantity", "unit_price", "line_total",
        ],
        "processing_logs": [
            "log_id", "file_name", "start_time", "end_time",
            "status", "error_message",
        ],
    }
    for table, expected_cols in checks.items():
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        actual_cols = {row["name"] for row in rows}
        missing = set(expected_cols) - actual_cols
        if missing:
            print(f"FAIL: Table '{table}' missing columns: {missing}")
            return False
    return True


def verify_foreign_keys(conn) -> bool:
    """Verify the FK from invoice_line_items → invoice_documents."""
    fks = conn.execute(
        "PRAGMA foreign_key_list(invoice_line_items)"
    ).fetchall()
    if not fks:
        print("FAIL: No foreign key on invoice_line_items")
        return False
    fk = fks[0]
    if fk["table"] != "invoice_documents" or fk["from"] != "document_id":
        print(f"FAIL: Unexpected FK: {dict(fk)}")
        return False
    return True


def main():
    print(f"Initializing database (target schema version {SCHEMA_VERSION})...")
    init_db()

    conn = get_connection()
    try:
        ok = True
        ok = verify_tables(conn) and ok
        ok = verify_columns(conn) and ok
        ok = verify_foreign_keys(conn) and ok

        if ok:
            print("OK: All tables, columns, indexes, and foreign keys verified.")
        else:
            print("ERRORS found — see above.")
            sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
