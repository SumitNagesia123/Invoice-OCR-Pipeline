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

---

## Step-by-Step Guide to Run Locally

Follow these steps to set up and run the InvoiceOCR pipeline in your local terminal:

### Step 1: Navigate to Project Root
Open your terminal (PowerShell or Bash) and navigate to the project repository folder.

### Step 2: Install Python Dependencies
Install the required packages listed in `requirements.txt`:
```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### Step 3: Configure External Binaries (Tesseract & Poppler)
Ensure you have **Tesseract OCR** and **Poppler** installed on your system:
- **Tesseract OCR**: Download and install from [UB-Mannheim/tesseract](https://github.com/UB-Mannheim/tesseract/wiki).
- **Poppler (for PDF support)**: Download and install from [Poppler for Windows](https://github.com/oschwartz10612/poppler-windows).

### Step 4: Configure Environment Variables (`.env`)
Copy the example environment file and update it with your MySQL credentials and binary paths:
```powershell
Copy-Item .env.example .env
```
Open `.env` in your editor and configure:
```env
DB_HOST=localhost
DB_PORT=3306
DB_NAME=invoice_ocr
DB_USER=root
DB_PASSWORD=your_mysql_password

TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
POPPLER_PATH=C:\Program Files\poppler\Library\bin

# Set to True if testing without an Anthropic API key
MOCK_EXTRACTOR=True
ANTHROPIC_API_KEY=your_actual_api_key_here
```

### Step 5: Initialize the Database
Run the setup script or let the application initialize the MySQL schema automatically:
```powershell
python -c "from src import db; db.init_db(); print('Database initialized successfully!')"
```

### Step 6: Add Invoices to the Input Folder
Place your invoice files (`.png`, `.jpg`, `.pdf`, etc.) into the `input_invoices/` folder:
```powershell
# Optional: generate synthetic sample test invoices
python -m src.generate_test_data
```

### Step 7: Run the Pipeline
Execute the main orchestrator to intake, preprocess, OCR, extract, validate, store, and report on all invoices:
```powershell
python src/main.py --no-email
```
*(Omit `--no-email` if you have configured SMTP settings in your `.env` to receive email summaries).*

### Step 8: View Results & Reports
- **Database Records**: Query MySQL (`invoice_ocr` database -> `invoice_documents`, `invoice_line_items`, `processing_logs`).
- **Generated Reports**: Check the `output/` directory for consolidated Excel sheets (`output/all_invoices.xlsx`) and HTML reports (`output/last_report.html`).

### Step 9: Run Tests
Run all unit, edge-case, and integration tests anytime to verify system health:
```powershell
python -m pytest
```
