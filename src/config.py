"""
InvoiceOCR Pipeline — Central configuration.

Loads settings from .env and provides path constants used across all modules.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# ── Folder paths ──────────────────────────────────────────────
INPUT_DIR = PROJECT_ROOT / "input_invoices"
PROCESSED_DIR = PROJECT_ROOT / "processed_invoices"
FAILED_DIR = PROJECT_ROOT / "failed_invoices"
OCR_OUTPUT_DIR = PROJECT_ROOT / "ocr_output"
LOG_DIR = PROJECT_ROOT / "logs"
DATA_DIR = PROJECT_ROOT / "data"

# ── Database (MySQL) ─────────────────────────────────────────
DB_HOST     = os.getenv("DB_HOST", "localhost")
DB_PORT     = os.getenv("DB_PORT", "3306")
DB_NAME     = os.getenv("DB_NAME", "invoice_ocr")
DB_USER     = os.getenv("DB_USER", "")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

# ── Tesseract ─────────────────────────────────────────────────
TESSERACT_CMD = os.getenv("TESSERACT_CMD")
if not TESSERACT_CMD:
    raise EnvironmentError("Missing required environment variable: TESSERACT_CMD in .env")

# ── Poppler (for pdf2image on Windows) ────────────────────────
POPPLER_PATH = os.getenv("POPPLER_PATH")
if not POPPLER_PATH:
    raise EnvironmentError("Missing required environment variable: POPPLER_PATH in .env")
POPPLER_PATH = Path(POPPLER_PATH)

# ── SMTP (email summary) ─────────────────────────────────────
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "")
SMTP_TO = os.getenv("SMTP_TO", "")

# ── Supported file extensions ─────────────────────────────────
SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif"}

# ── Mock Mode ────────────────────────────────────────────────
MOCK_EXTRACTOR = os.getenv("MOCK_EXTRACTOR", "false").lower() == "true"

if __name__ == '__main__':
    print("Configuration loaded successfully:")
    print(f"  DB_HOST: {DB_HOST}")
    print(f"  DB_NAME: {DB_NAME}")
    print(f"  TESSERACT_CMD: {TESSERACT_CMD}")
    print(f"  MOCK_EXTRACTOR: {MOCK_EXTRACTOR}")
