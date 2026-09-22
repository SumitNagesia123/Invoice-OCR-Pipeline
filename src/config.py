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

# ── Database ──────────────────────────────────────────────────
DATABASE_PATH = PROJECT_ROOT / os.getenv("DATABASE_PATH", "data/invoices.db")

# ── Tesseract ─────────────────────────────────────────────────
TESSERACT_CMD = os.getenv(
    "TESSERACT_CMD",
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
)

# ── Poppler (for pdf2image on Windows) ────────────────────────
POPPLER_PATH = os.getenv("POPPLER_PATH", "") or None

# ── SMTP (email summary) ─────────────────────────────────────
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "")
SMTP_TO = os.getenv("SMTP_TO", "")

# ── Supported file extensions ─────────────────────────────────
SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif"}
