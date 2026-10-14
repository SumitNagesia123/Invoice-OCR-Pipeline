# InvoiceOCR Pipeline

A lightweight, local-first pipeline to process invoices from folders, perform OCR, parse structured data, and store it in a SQLite database.

## Components
- **`intake.py`**: Monitors an input directory, validates files, and performs duplicate detection.
- **`ocr.py`**: Converts PDFs/images to text using Tesseract.
- **`extractor.py`**: Extracts structured fields (invoice number, date, vendor, total, line items) from OCR text using Claude 3.5 Sonnet (LLM) or a mock extractor for testing.
- **`storage.py`**: Manages SQLite storage for processed documents.
- **`db.py` / `init_db.py`**: DB schema management and initialization.
- **`reporter.py`**: Generates and sends daily status email summaries.
- **`config.py`**: Central configuration (settings, environment variables like MOCK_EXTRACTOR).

## Setup

1. **Prerequisites**
   - Install [Tesseract OCR](https://github.com/tesseract-ocr/tesseract)
   - Install Poppler (required for PDF processing via `pdf2image` on Windows).
   - [pyodbc](https://github.com/mkleehammer/pyodbc) and ODBC driver for SQL Server.

2. **Configuration**
   Create a `.env` file in the project root and fill in the required values:
   - `TESSERACT_CMD`: Path to `tesseract.exe`.
   - `POPPLER_PATH`: Path to Poppler's `bin/` directory.
   - **Database (SQL Server)**: `DB_SERVER`, `DB_NAME`, `DB_DRIVER`, `DB_TRUSTED`, `DB_USER`, `DB_PASSWORD`.
   - **SMTP settings**: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_TO` (required for automated email summaries).
   - `MOCK_EXTRACTOR`: Set to `true` to enable mock extraction mode for benchmarking (no LLM costs).

## Running the Pipeline
   ```bash
   python init_db.py
   python run_pipeline.py
   ```
This script scans `input_invoices/`, performs OCR on all supported files (images and PDFs), parses the output, and stores the results in the database. Successful files are moved to `processed_invoices/`, while failed files are moved to `failed_invoices/`.

## Running Tests
This project uses `pytest`. Run all tests with:
```bash
pytest
```
