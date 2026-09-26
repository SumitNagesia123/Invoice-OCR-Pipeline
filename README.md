# InvoiceOCR Pipeline

Automated batch processing pipeline for extracting and storing invoice data from images and PDFs.

## Project Overview
This project provides an automated, robust pipeline for ingesting, parsing, and storing data from invoice documents. It leverages Tesseract OCR for text extraction and a heuristic-based parsing engine to structure invoice data into a relational database.

## Key Features
- **Intake Pipeline**: Automated scanning of input folders with file sanitization and deduplication.
- **OCR Engine**: Advanced image preprocessing (OpenCV) followed by Tesseract-based text extraction.
- **Smart Parsing**: Heuristic-based regex engine to extract key fields (Invoice #, Date, Vendor, Amount, Line Items) with confidence scoring.
- **Relational Storage**: SQLite-backed database with normalized tables for documents, line items, and transaction logs.
- **Resilience**: Full suite of 270+ unit, integration, and edge-case tests.

## Architecture
- **Intake**: src/intake.py handles file validation, hashing, and sanitization.
- **OCR**: src/ocr.py processes raw images (grayscale, denoising, deskewing) before OCR.
- **Parser**: src/parser.py parses unstructured text into structured JSON data.
- **Storage**: src/storage.py interfaces with SQLite to ensure data integrity.
- **DB**: src/db.py contains schema definitions and migration logic.

## Setup Instructions

### 1. Prerequisites
- **Python 3.10+** installed.
- **Tesseract OCR (v5+)**: Essential for OCR functionality.
- **Poppler (Optional)**: Required if processing PDF invoices.

### 2. Installation
`powershell
# Clone the repository
git clone https://github.com/SumitNagesia123/Invoice-OCR-Pipeline.git
cd Invoice-OCR-Pipeline

# Setup virtual environment
python -m venv venv
.\venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
`

### 3. Configuration
Copy .env.example to .env and fill in necessary paths:
`ini
TESSERACT_CMD=C:\YOUR\PATH\TO\tesseract.exe
POPPLER_PATH=C:\YOUR\PATH\TO\poppler\bin
DATABASE_PATH=data/invoices.db
`

### 4. Initialization
Run the database initializer to prepare the storage schema:
`powershell
python init_db.py
`

## How It Works
1. **Drop**: Move your invoice files (.png, .jpg, .pdf) into the input_invoices/ folder.
2. **Execute**: Run the pipeline:
   `powershell
   python run_pipeline.py
   `
3. **Outcome**:
   - Files are sanitized and checked for duplicates.
   - OCR extracts raw text.
   - Parser structures the data.
   - Final data is stored in data/invoices.db.
   - Processed files are moved to processed_invoices/.

## Dashboard
A web-based dashboard is provided in dashboard.html to visualize pipeline health, documents, and logs. Simply open it in any modern browser.
