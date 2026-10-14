"""
InvoiceOCR Pipeline — Database initialization and access.
"""

import logging
import mysql.connector
from src import config

logger = logging.getLogger(__name__)

def get_connection():
    return mysql.connector.connect(
        host=config.DB_HOST,
        port=config.DB_PORT,
        database=config.DB_NAME,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
    )

def init_db_for_test():
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
    finally:
         cursor.execute("SET FOREIGN_KEY_CHECKS = 1;")
         conn.commit()
         cursor.close()
         conn.close()
    logger.info("Database truncated for test run.")
