import sys
from pathlib import Path
import os
from src import intake, config, db
from tests import db_setup

# Ensure we always use the test database
config.DB_NAME = "invoice_test"
config.MOCK_EXTRACTOR = True
db.init_db()

print(f"--- TASK: Run Mock Batch ---")
print(f"Using database: {config.DB_NAME}")
print(f"Extractor mode: MOCK")

if not config.MOCK_EXTRACTOR:
    print("ERROR: Extractor must be in mock mode.")
    sys.exit(1)

# Safe truncation (db_setup now has runtime safety checks)
db_setup.init_db_for_test()

# Run the batch
print("Running intake...")
results = intake.scan_input_folder()
print(f"Intake finished.")

# Dummy processing for verification
from src import storage, extractor
for result in results:
    if result["status"] == "ready":
         parsed_data = extractor.extract_invoice_data("ocr_text_placeholder")
         storage.store_invoice(
             result["file_name"],
             result["file_hash"],
             "ocr_path_dummy",
             parsed_data
         )

print("\n--- ACTUAL SQL RESULTS ---")
conn = db.get_connection()
cursor = conn.cursor()

print("\nSELECT status, COUNT(*) FROM invoice_documents GROUP BY status;")
cursor.execute("SELECT status, COUNT(*) FROM invoice_documents GROUP BY status;")
print(cursor.fetchall())

print("\nSELECT COUNT(*) FROM invoice_documents;")
cursor.execute("SELECT COUNT(*) FROM invoice_documents;")
print(cursor.fetchone()[0])

print("\nSELECT file_hash, COUNT(*) FROM invoice_documents GROUP BY file_hash HAVING COUNT(*) > 1;")
cursor.execute("SELECT file_hash, COUNT(*) FROM invoice_documents GROUP BY file_hash HAVING COUNT(*) > 1;")
print(cursor.fetchall())

print("\nMissing reasons check:")
cursor.execute("SELECT COUNT(*) FROM invoice_documents WHERE status IN ('needs_review', 'failed') AND review_reason IS NULL AND failure_reason IS NULL;")
print(cursor.fetchone()[0])

conn.close()
