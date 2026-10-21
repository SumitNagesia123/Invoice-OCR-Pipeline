"""
InvoiceOCR Pipeline — Test Database setup and utilities.
"""

import logging
import mysql.connector
from src import config, db

logger = logging.getLogger(__name__)

def ensure_test_database():
    conn = mysql.connector.connect(
        host=config.DB_HOST,
        port=config.DB_PORT,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
    )
    cursor = conn.cursor()
    cursor.execute("CREATE DATABASE IF NOT EXISTS `invoice_test`")
    conn.commit()
    cursor.close()
    conn.close()
    config.DB_NAME = "invoice_test"

def get_connection():
    ensure_test_database()
    return mysql.connector.connect(
        host=config.DB_HOST,
        port=config.DB_PORT,
        database="invoice_test",
        user=config.DB_USER,
        password=config.DB_PASSWORD,
    )

def init_db_for_test():
    ensure_test_database()
    db.init_db()
    conn = get_connection()
    cursor = conn.cursor()

    # 1. Safety check
    cursor.execute("SELECT DATABASE();")
    current_db = cursor.fetchone()[0]
    if current_db != "invoice_test":
        cursor.close()
        conn.close()
        raise RuntimeError(f"Refusing to truncate: Connected to '{current_db}', expected 'invoice_test'")

    # 2. Safely clear for tests without FK issues
    cursor.execute("SET FOREIGN_KEY_CHECKS = 0;")
    try:
         cursor.execute("TRUNCATE TABLE invoice_line_items;")
         cursor.execute("TRUNCATE TABLE invoice_documents;")
         cursor.execute("TRUNCATE TABLE processing_logs;")
         cursor.execute("TRUNCATE TABLE duplicate_files;")
    finally:
         cursor.execute("SET FOREIGN_KEY_CHECKS = 1;")
         conn.commit()
         cursor.close()
         conn.close()
    logger.info("Database truncated for test run.")
