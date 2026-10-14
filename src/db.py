"""
InvoiceOCR Pipeline — Database initialization and access.

Provides schema creation, connection helpers, and versioned migrations
for MySQL via mysql-connector-python.
"""

import logging
import mysql.connector
from mysql.connector import errorcode
from src import config

logger = logging.getLogger(__name__)

# ── Schema version ────────────────────────────────────────────
# Bump this and add a migration function when the schema changes.
SCHEMA_VERSION = 4


# ── Connection ────────────────────────────────────────────────

def get_connection() -> mysql.connector.MySQLConnection:
    """Return a connection to the MySQL database."""
    return mysql.connector.connect(
        host=config.DB_HOST,
        port=config.DB_PORT,
        database=config.DB_NAME,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
    )


def row_to_dict(cursor, row) -> dict:
    """Convert a row to a dict using cursor.description."""
    if row is None:
        return None
    columns = [col[0] for col in cursor.description]
    return dict(zip(columns, row))


# ── SQL statements (MySQL) ────────────────────────────────────

CREATE_SCHEMA_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_version (
    version     INT             NOT NULL,
    applied_at  TIMESTAMP       DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_INVOICE_DOCUMENTS = """
CREATE TABLE IF NOT EXISTS invoice_documents (
    document_id     INT             AUTO_INCREMENT PRIMARY KEY,
    file_name       VARCHAR(500)    NOT NULL UNIQUE,
    file_hash       CHAR(64)        NULL,
    invoice_number  VARCHAR(50),
    invoice_date    DATE,
    vendor_name     VARCHAR(200),
    total_amount    DECIMAL(10,2),
    currency        VARCHAR(3),
    status          VARCHAR(20)     NOT NULL DEFAULT 'needs_review',
    ocr_text_path   VARCHAR(500),
    processed_at    TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,
    review_reason   TEXT            NULL,
    failure_reason  TEXT            NULL,
    original_file   VARCHAR(255)    NULL,
    INDEX(file_hash)
);
"""

CREATE_INVOICE_LINE_ITEMS = """
CREATE TABLE IF NOT EXISTS invoice_line_items (
    line_item_id    INT             AUTO_INCREMENT PRIMARY KEY,
    document_id     INT             NOT NULL,
    description     VARCHAR(200),
    quantity        DECIMAL(18,4),
    unit_price      DECIMAL(10,2),
    line_total      DECIMAL(10,2),
    FOREIGN KEY (document_id) REFERENCES invoice_documents(document_id)
        ON DELETE CASCADE
);
"""

CREATE_PROCESSING_LOGS = """
CREATE TABLE IF NOT EXISTS processing_logs (
    log_id          INT             AUTO_INCREMENT PRIMARY KEY,
    file_name       VARCHAR(500)    NOT NULL,
    start_time      TIMESTAMP       NOT NULL,
    end_time        TIMESTAMP       NULL,
    status          VARCHAR(20)     NOT NULL DEFAULT 'running',
    error_message   TEXT,
    INDEX(file_name)
);
"""

CREATE_DUPLICATE_FILES = """
CREATE TABLE IF NOT EXISTS duplicate_files (
    duplicate_id    INT             AUTO_INCREMENT PRIMARY KEY,
    file_name       VARCHAR(500)    NOT NULL,
    file_hash       CHAR(64)        NOT NULL,
    original_file   VARCHAR(500)    NOT NULL,
    skipped_at      TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,
    INDEX(file_hash)
);
"""


def _get_current_version(conn: mysql.connector.MySQLConnection) -> int:
    """Return the current schema version, or 0 if the table doesn't exist."""
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT MAX(version) FROM schema_version")
        row = cursor.fetchone()
        return row[0] if row and row[0] is not None else 0
    except mysql.connector.Error:
        return 0
    finally:
        cursor.close()


def _apply_v1(conn: mysql.connector.MySQLConnection) -> None:
    """Version 1 — initial schema."""
    cursor = conn.cursor()
    try:
        cursor.execute(CREATE_SCHEMA_VERSION_TABLE)
        cursor.execute(CREATE_INVOICE_DOCUMENTS)
        cursor.execute(CREATE_INVOICE_LINE_ITEMS)
        cursor.execute(CREATE_PROCESSING_LOGS)
        conn.commit()
        logger.info("Applied schema version 1.")
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()

def _apply_v2_migration(conn: mysql.connector.MySQLConnection) -> None:
    """Version 2 — add metadata columns and unique hash safely."""
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT COUNT(*) FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'invoice_documents'
              AND COLUMN_NAME = 'original_file'
        """)
        if cursor.fetchone()[0] == 0:
            query = """
            SET FOREIGN_KEY_CHECKS = 0;
            TRUNCATE TABLE invoice_documents;
            TRUNCATE TABLE invoice_line_items;
            SET FOREIGN_KEY_CHECKS = 1;

            ALTER TABLE invoice_documents
            MODIFY COLUMN file_hash CHAR(64) NOT NULL,
            ADD UNIQUE INDEX idx_file_hash (file_hash),
            ADD COLUMN review_reason TEXT NULL,
            ADD COLUMN failure_reason TEXT NULL,
            ADD COLUMN original_file VARCHAR(255) NULL;
            """
            for stmt in query.split(';'):
                if stmt.strip():
                    cursor.execute(stmt)
            conn.commit()
            logger.info("Applied schema version 2.")
        else:
            logger.info("Schema v2 columns already exist. Skipping Truncate/Alter.")
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()

def _apply_v3_migration(conn: mysql.connector.MySQLConnection) -> None:
    """Version 3 — make file_hash nullable for failed invoices."""
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE invoice_documents MODIFY COLUMN file_hash CHAR(64) NULL;")
        conn.commit()
        logger.info("Applied schema version 3.")
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()

def _apply_v4_migration(conn: mysql.connector.MySQLConnection) -> None:
    """Version 4 — create duplicate_files table, migrate old duplicate_skipped rows, remove from invoice_documents."""
    cursor = conn.cursor()
    try:
        cursor.execute(CREATE_DUPLICATE_FILES)

        # Migrate any existing duplicate_skipped rows to duplicate_files
        cursor.execute("""
            INSERT INTO duplicate_files (file_name, file_hash, original_file, skipped_at)
            SELECT file_name, COALESCE(file_hash, 'UNKNOWN'), COALESCE(original_file, 'UNKNOWN'), processed_at
            FROM invoice_documents
            WHERE status = 'duplicate_skipped'
        """)

        # Delete duplicate_skipped rows from invoice_documents
        cursor.execute("DELETE FROM invoice_documents WHERE status = 'duplicate_skipped'")

        conn.commit()
        logger.info("Applied schema version 4.")
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()

# Map of version -> migration function.
MIGRATIONS = {
    1: _apply_v1,
    2: _apply_v2_migration,
    3: _apply_v3_migration,
    4: _apply_v4_migration,
}


def init_db() -> None:
    """Create or migrate the database to the latest schema version."""
    conn = get_connection()
    try:
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
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO schema_version (version) VALUES (%s)", (ver,)
            )
            conn.commit()
            cursor.close()
            logger.info("Migrated to schema version %d.", ver)

        logger.info("Database is up to date (version %d).", SCHEMA_VERSION)
    finally:
        conn.close()
