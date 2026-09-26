# InvoiceOCR Pipeline

Automated batch processing pipeline for extracting and storing invoice data from images and PDFs using Tesseract OCR and SQLite.

## About This Project
The InvoiceOCR Pipeline is an automated, robust document processing system designed to bring order to unstructured invoice data. By integrating cutting-edge OCR technology with a proprietary parsing engine, it transforms disparate invoice documents into structured, queryable data. This project was developed as a comprehensive engineering solution to facilitate reliable data extraction, storage, and reporting.

## 📁 Project Structure

```
Invoice-OCR-Pipeline/
│
├── data/                    # SQLite database storage
├── input_invoices/          # Drop zone for new invoice files
├── processed_invoices/      # Successfully parsed invoices
├── failed_invoices/         # Files that failed processing
├── ocr_output/              # Intermediate raw OCR text output
├── logs/                    # Application-level logs
│
├── src/                     # Core application source code
│   ├── config.py            # Configuration & environment loading
│   ├── db.py                # Schema initialization & connectivity
│   ├── intake.py            # File intake, validation, sanitization
│   ├── ocr.py                # Image preprocessing & OCR engine
│   ├── parser.py            # Field extraction & heuristic engine
│   ├── storage.py           # Database interaction & validation
│   └── reporter.py          # Email/report generation logic
│
├── tests/                   # 270+ unit, integration & edge-case tests
│
├── init_db.py               # Database schema setup script
├── run_pipeline.py          # Main application entry point
├── dashboard.html           # Interactive web dashboard
├── requirements.txt         # Python dependencies
├── .env.example              # Environment variable template
└── README.md                 # Project documentation
```

## Detailed Workflow Breakdown

### 1. Intake and Validation
The pipeline begins by scanning input_invoices/. Every incoming file undergoes several checks before processing:
- **Format Validation**: Only PNG, JPG, TIFF, and PDF are accepted.
- **Sanitization**: Filenames are sanitized to prevent path-traversal attacks.
- **Deduplication**: A SHA-256 hash is computed for each file and compared against the database to ensure no duplicate documents are processed.

### 2. OCR Preprocessing and Extraction
Once validated, images are prepared for OCR:
- **Preprocessing**: Using OpenCV, images are converted to grayscale, denoised, contrast-normalized (CLAHE), and deskewed.
- **OCR Engine**: Tesseract (v5+) extracts text from the enhanced images.
- **PDF Handling**: PDFs are converted to images using Poppler prior to text extraction.

### 3. Parsing and Heuristics
The parsing engine takes the raw OCR text and attempts to structure it:
- **Regex Extraction**: Uses regex heuristics to identify Vendor Names, Invoice IDs, Dates (standardized to ISO YYYY-MM-DD), and Amount (Total + Currency).
- **Confidence Scoring**: Each invoice receives a confidence score (High/Medium/Low) based on the number of successfully extracted key fields.

### 4. Relational Storage
Data is inserted into a normalized SQLite database after server-side validation.
- **Document Table**: Stores file metadata, parsed invoice fields, and status.
- **Line Item Table**: Stores individual line items linked via foreign key to the document.
- **Audit Logs**: Every processing attempt is logged with start/end times and error messaging for debugging.

## Setup Instructions

### Prerequisites
- Python 3.10+
- Tesseract OCR (v5.x+)
- Poppler (for PDF support)

### Installation
1. Clone: git clone https://github.com/SumitNagesia123/Invoice-OCR-Pipeline.git
2. Install: pip install -r requirements.txt
3. Configure: Copy .env.example to .env and fill the variables (Tesseract path, DB path).
4. Initialize DB: python init_db.py

### Usage
- Add files to input_invoices/.
- Run: python run_pipeline.py.
- Open dashboard.html in your browser to view processing status and database reports.


