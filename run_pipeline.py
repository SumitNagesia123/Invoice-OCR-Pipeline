import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src import intake, ocr, parser, storage

def run_pipeline():
    print("Starting pipeline scan...")
    # 1. Intake
    results = intake.scan_input_folder()

    for res in results:
        file_path = res["file_path"]
        log_id = res["log_id"]

        if res["status"] != "ready":
            print(f"Skipping or Failed: {res['file_name']} - {res['reason']}")
            continue

        print(f"Processing: {res['file_name']}")

        # 2. OCR
        ocr_result = ocr.extract_text(file_path)
        if not ocr_result["success"]:
            print(f"OCR Failed for {res['file_name']}: {ocr_result['error']}")
            storage.finalize_processing_log(log_id, success=False, error_message=ocr_result['error'])
            intake.move_to_failed(file_path, ocr_result['error'])
            continue

        # 3. Parse
        parsed_data = parser.parse_invoice_text(ocr_result["text"])

        # 4. Store
        try:
            doc_id = storage.store_invoice(
                res["file_name"],
                res["file_hash"],
                ocr_result["ocr_text_path"],
                parsed_data
            )
            # 5. Finalize
            storage.finalize_processing_log(log_id, success=True)
            storage.move_to_processed(file_path)
            print(f"Successfully processed {res['file_name']} (DocID: {doc_id})")
        except Exception as e:
            print(f"Storage failed for {res['file_name']}: {e}")
            storage.finalize_processing_log(log_id, success=False, error_message=str(e))
            intake.move_to_failed(file_path, str(e))

if __name__ == "__main__":
    run_pipeline()
