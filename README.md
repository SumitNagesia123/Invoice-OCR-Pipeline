# InvoiceOCR Pipeline

A lightweight, local-first pipeline to process invoices from folders, perform OCR, parse structured data via regex or LLM (Claude), and store results in a MySQL database with automated duplicate detection, reporting, and email summaries.

## Components

- **`intake.py`**: Monitors an input directory, validates files, computes SHA-256 hashes, handles empty files, detects duplicates (batch + database), and routes unreadable or unsupported files to the failed folder.
- **`ocr.py`**: Converts PDFs and images to text using Tesseract, with a preprocessing pipeline (grayscale, denoising, CLAHE contrast enhancement, deskewing, and adaptive thresholding).
- **`extractor.py`**: Extracts structured fields (invoice number, date, vendor, total, line items) using Anthropic Claude 3.5 Sonnet or a mock extractor for testing.
- **`schema.py`**: Validates extracted data using Pydantic models with automatic line total calculation and subtotal/tax/total consistency checks.
- **`storage.py`**: Manages MySQL storage for processed documents, line items, duplicate logs, and processing logs.
- **`db.py`**: Database initialization and versioned schema migrations (v1 to v4).
- **`reporter.py`**: Generates multi-sheet Excel reports and HTML email summaries.
- **`emailer.py`**: Sends daily summary emails via SMTP with Excel attachments and dry-run/fallback support.
- **`main.py`**: Orchestrator linking intake, OCR, extraction, storage, reporting, and emailing.

## Setup

### 1. Prerequisites
- Install [Tesseract OCR](https://github.com/tesseract-ocr/tesseract)
- Install Poppler (required for PDF processing via `pdf2image` on Windows).
- Install a local or remote MySQL server.

### 2. Installation
```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configuration
Copy `.env.example` to `.env` and configure your environment:
- `TESSERACT_CMD`: Path to `tesseract.exe`.
- `POPPLER_PATH`: Path to Poppler's `bin/` directory.
- **Database (MySQL)**: `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`.
- `MOCK_EXTRACTOR`: Set to `true` for mock extraction (no LLM cost), `false` for live Anthropic API calls.
- `ANTHROPIC_API_KEY`: Required if `MOCK_EXTRACTOR=false`.
- **SMTP settings**: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_TO`.

## Running the Pipeline

```bash
python -m src.main
```

This scans `input_invoices/`, performs OCR, extracts structured data, and stores results in MySQL. Successful files move to `processed_invoices/`, failed files to `failed_invoices/`.

## Running Tests

This project uses `pytest`. Run all tests with:
```bash
pytest
```
