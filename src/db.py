"""
InvoiceOCR Pipeline — Database initialization and access.

Provides schema creation, connection helpers, and versioned migrations
for the SQLite database. Schema is designed to be portable to
PostgreSQL/SQL Server later.
"""

import sqlite3
import logging
from pathlib import Path

from src import config

logger = logging.getLogger(__name__)

# ── Schema version ────────────────────────────────────────────
# Bump this and add a migration function when the schema changes.
SCHEMA_VERSION = 1

# ── SQL statements ────────────────────────────────────────────

CREATE_SCHEMA_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER     NOT NULL,
    applied_at  TIMESTAMP   DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_INVOICE_DOCUMENTS = """
CREATE TABLE IF NOT EXISTS invoice_documents (
    document_id     INTEGER     PRIMARY KEY AUTOINCREMENT,
    file_name       TEXT        NOT NULL UNIQUE,
    file_hash       TEXT        NOT NULL,
    invoice_number  TEXT,
    invoice_date    DATE,
    vendor_name     TEXT,
    total_amount    DECIMAL(10,2),
    currency        TEXT(3),
    status          TEXT        NOT NULL DEFAULT 'needs_review'
                                CHECK (status IN ('processed', 'needs_review', 'failed')),
    ocr_text_path   TEXT,
    processed_at    TIMESTAMP   DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_INVOICE_LINE_ITEMS = """
CREATE TABLE IF NOT EXISTS invoice_line_items (
    line_item_id    INTEGER     PRIMARY KEY AUTOINCREMENT,
    document_id     INTEGER     NOT NULL,
    description     TEXT,
    quantity        DECIMAL,
    unit_price      DECIMAL(10,2),
    line_total      DECIMAL(10,2),
    FOREIGN KEY (document_id) REFERENCES invoice_documents(document_id)
        ON DELETE CASCADE
);
"""

CREATE_PROCESSING_LOGS = """
CREATE TABLE IF NOT EXISTS processing_logs (
    log_id          INTEGER     PRIMARY KEY AUTOINCREMENT,
    file_name       TEXT        NOT NULL,
    start_time      TIMESTAMP   NOT NULL,
    end_time        TIMESTAMP,
    status          TEXT        NOT NULL DEFAULT 'running'
                                CHECK (status IN ('success', 'failed', 'running')),
    error_message   TEXT
);
"""

CREATE_INDEX_FILE_NAME = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_invoice_documents_file_name
    ON invoice_documents(file_name);
"""

CREATE_INDEX_FILE_HASH = """
CREATE INDEX IF NOT EXISTS idx_invoice_documents_file_hash
    ON invoice_documents(file_hash);
"""

CREATE_INDEX_LINE_ITEMS_DOC_ID = """
CREATE INDEX IF NOT EXISTS idx_invoice_line_items_document_id
    ON invoice_line_items(document_id);
"""

CREATE_INDEX_LOGS_FILE_NAME = """
CREATE INDEX IF NOT EXISTS idx_processing_logs_file_name
    ON processing_logs(file_name);
"""


def get_connection() -> sqlite3.Connection:
    """Return a connection to the SQLite database with FK enforcement."""
    db_path = config.DATABASE_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.row_factory = sqlite3.Row
    return conn


def _get_current_version(conn: sqlite3.Connection) -> int:
    """Return the current schema version, or 0 if the table doesn't exist."""
    try:
        row = conn.execute(
            "SELECT MAX(version) AS v FROM schema_version"
        ).fetchone()
        return row["v"] if row and row["v"] is not None else 0
    except sqlite3.OperationalError:
        return 0


def _apply_v1(conn: sqlite3.Connection) -> None:
    """Version 1 — initial schema."""
    conn.executescript(
        CREATE_INVOICE_DOCUMENTS
        + CREATE_INVOICE_LINE_ITEMS
        + CREATE_PROCESSING_LOGS
        + CREATE_INDEX_FILE_NAME
        + CREATE_INDEX_FILE_HASH
        + CREATE_INDEX_LINE_ITEMS_DOC_ID
        + CREATE_INDEX_LOGS_FILE_NAME
    )
    logger.info("Applied schema version 1.")


# Map of version -> migration function.  Add new entries when the schema
# evolves (e.g., 2: _apply_v2).
MIGRATIONS = {
    1: _apply_v1,
}


def init_db() -> None:
    """
    Create or migrate the database to the latest schema version.

    Safe to call repeatedly — already-applied versions are skipped.
    """
    conn = get_connection()
    try:
        conn.executescript(CREATE_SCHEMA_VERSION_TABLE)
        current = _get_current_version(conn)

        if current >= SCHEMA_VERSION:
            logger.info(
                "Database already at version %d — nothing to migrate.",
                current,
            )
            return

        for ver in range(current + 1, SCHEMA_VERSION + 1):
            migration = MIGRATIONS.get(ver)
            if migration is None:
                raise RuntimeError(f"Missing migration for version {ver}")
            migration(conn)
            conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (ver,)
            )
            conn.commit()
            logger.info("Migrated to schema version %d.", ver)

        logger.info("Database is up to date (version %d).", SCHEMA_VERSION)
    finally:
        conn.close()
