#!/usr/bin/env python
"""
Initialize (or migrate) the InvoiceOCR database (MySQL).

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
    """Check that all expected tables exist using INFORMATION_SCHEMA."""
    cursor = conn.cursor()
    expected_tables = {"schema_version", "invoice_documents",
                       "invoice_line_items", "processing_logs"}

    cursor.execute("SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE'")
    actual_tables = {row[0] for row in cursor.fetchall()}
    cursor.close()

    missing = expected_tables - actual_tables
    if missing:
        print(f"FAIL: Missing tables: {missing}")
        return False
    return True


def verify_columns(conn) -> bool:
    """Spot-check that key columns exist."""
    cursor = conn.cursor()
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
        cursor.execute("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s", (table,))
        actual_cols = {row[0] for row in cursor.fetchall()}
        missing = set(expected_cols) - actual_cols
        if missing:
            print(f"FAIL: Table '{table}' missing columns: {missing}")
            cursor.close()
            return False
    cursor.close()
    return True


def verify_foreign_keys(conn) -> bool:
    """Verify the FK from invoice_line_items -> invoice_documents."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT REFERENCED_TABLE_NAME, COLUMN_NAME
        FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
        WHERE TABLE_SCHEMA = DATABASE()
        AND TABLE_NAME = 'invoice_line_items'
        AND CONSTRAINT_NAME != 'PRIMARY'
    """)
    fk = cursor.fetchone()
    cursor.close()

    if not fk:
        print("FAIL: No foreign key on invoice_line_items")
        return False
    if fk[0] != "invoice_documents" or fk[1] != "document_id":
        print(f"FAIL: Unexpected FK: {fk}")
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
            print("OK: All tables, columns, and foreign keys verified.")
        else:
            print("ERRORS found — see above.")
            sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
