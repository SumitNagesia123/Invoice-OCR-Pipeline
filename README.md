# InvoiceOCR Pipeline

Automated batch processing pipeline for extracting and storing invoice data from images and PDFs.

## Setup

1. **Install Tesseract OCR**:
   - Download and install Tesseract from [Tesseract at UB-Mannheim](https://github.com/UB-Mannheim/tesseract/wiki).
   - Note the installation path (default: `C:\Program Files\Tesseract-OCR\tesseract.exe`).

2. **Install Poppler (for PDF support on Windows)**:
   - Download from [Poppler for Windows](https://github.com/oschwartz10612/poppler-windows/releases).
   - Extract to a folder and add `bin` to your PATH, or set the path in `.env`.

3. **Python Environment**:
   - Create a virtual environment: `python -m venv venv`
   - Activate it: `.\venv\Scripts\activate` (Windows)
   - Install dependencies: `pip install -r requirements.txt`

4. **Configuration**:
   - Copy `.env.example` to `.env`.
   - Update `TESSERACT_CMD`, `POPPLER_PATH`, and SMTP settings as needed.

5. **Initialize Database**:
   - Run `python init_db.py`.

## Running the Pipeline

- Place invoice files (`.png`, `.jpg`, `.pdf`, `.tiff`, `.tif`) in `input_invoices/`.
- Run the intake-processing loop (implementation pending in main entry point).

## Testing

Run the test suite: `pytest` or `python -m unittest discover tests`.
