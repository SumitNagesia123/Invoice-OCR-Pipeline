"""
InvoiceOCR Pipeline — Main entry point (the Orchestrator).
"""

import sys
import argparse
from pathlib import Path
import logging
from datetime import datetime, timedelta

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import intake, ocr, extractor, storage, reporter, emailer, config, db

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

def process_file(result: dict) -> None:
    """Run OCR, extraction, and storage for a single file."""
    try:
        ocr_result = ocr.extract_text(result["file_path"])
        if not ocr_result["success"]:
            raise Exception(ocr_result["error"])

        ocr_path = ocr_result["ocr_text_path"]

        parsed_data = extractor.extract_invoice_data(ocr_result["text"])

        storage.store_invoice(
            result["file_name"],
            result["file_hash"],
            ocr_path,
            parsed_data,
            review_reason=None,
            failure_reason=None
        )

        storage.finalize_processing_log(result["log_id"], success=True)
        storage.move_to_processed(result["file_path"])

    except Exception as e:
        logger.error(f"Processing failed for {result['file_name']}: {e}")
        storage.store_failed_invoice(result['file_name'], result['file_hash'], str(e))
        storage.finalize_processing_log(result["log_id"], success=False, error_message=str(e))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Do not send emails, just log and save report.")
    parser.add_argument("--no-email", action="store_true", help="Do not send emails.")
    args = parser.parse_args()

    run_start_time = datetime.now()

    logger.info("Starting automated invoice processing cycle (Mock: %s). Dry-run: %s, No-email: %s", config.MOCK_EXTRACTOR, args.dry_run, args.no_email)

    db.init_db()

    intake_results = intake.scan_input_folder()

    for result in intake_results:
        if result["status"] == "ready":
             process_file(result)

    until = datetime.now()
    since = run_start_time

    excel_path, html = reporter.run_reporter(since, until)

    if not args.no_email:
        subject = f"Invoice Processing Report: {datetime.now().strftime('%Y-%m-%d')} ({len(intake_results)} files)"
        if config.MOCK_EXTRACTOR:
            subject = f"[MOCK MODE] {subject}"
        emailer.send_daily_summary(excel_path=excel_path, html_body=html, subject=subject, dry_run=args.dry_run)
    else:
        logger.info("Email disabled: skipping.")

if __name__ == "__main__":
    main()
