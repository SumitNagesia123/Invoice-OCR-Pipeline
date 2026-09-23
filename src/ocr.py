"""
InvoiceOCR Pipeline — OCR extraction.

Preprocesses invoice images (deskew, denoise, contrast), runs Tesseract
OCR to extract text, and saves raw output to ocr_output/ for traceability.
Handles both image files and PDFs (via pdf2image + Poppler).
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
import pytesseract
from PIL import Image

from src import config

logger = logging.getLogger(__name__)

# Point pytesseract at the Tesseract executable
pytesseract.pytesseract.tesseract_cmd = config.TESSERACT_CMD


# ── Image preprocessing ──────────────────────────────────────

def _to_grayscale(img: np.ndarray) -> np.ndarray:
    """Convert to grayscale if the image has color channels."""
    if len(img.shape) == 3:
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return img


def _denoise(img: np.ndarray) -> np.ndarray:
    """Apply light denoising to reduce scan artifacts."""
    return cv2.fastNlMeansDenoising(img, h=10)


def _enhance_contrast(img: np.ndarray) -> np.ndarray:
    """Apply CLAHE (adaptive histogram equalization) for better contrast."""
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return clahe.apply(img)


def _threshold(img: np.ndarray) -> np.ndarray:
    """Apply adaptive thresholding to produce a clean black/white image."""
    return cv2.adaptiveThreshold(
        img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY, 11, 2,
    )


def _deskew(img: np.ndarray) -> np.ndarray:
    """
    Detect and correct small rotations (skew) in scanned documents.

    Uses minAreaRect on non-zero pixels to estimate the skew angle.
    Only corrects angles within ±15° to avoid flipping.
    """
    coords = np.column_stack(np.where(img > 0))
    if len(coords) < 10:
        return img

    angle = cv2.minAreaRect(coords)[-1]

    # minAreaRect returns angles in [-90, 0).  Normalize:
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle

    # Only correct small skew
    if abs(angle) > 15 or abs(angle) < 0.5:
        return img

    h, w = img.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(
        img, M, (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )
    logger.debug("Deskewed by %.2f°", angle)
    return rotated


def preprocess_image(img: np.ndarray) -> np.ndarray:
    """
    Full preprocessing pipeline for a scanned invoice image.

    Steps: grayscale → denoise → contrast → deskew → threshold.
    """
    img = _to_grayscale(img)
    img = _denoise(img)
    img = _enhance_contrast(img)
    img = _deskew(img)
    img = _threshold(img)
    return img


# ── PDF handling ──────────────────────────────────────────────

def _pdf_to_images(file_path: Path) -> list[np.ndarray]:
    """
    Convert each page of a PDF to a numpy array (image).

    Requires Poppler to be installed and POPPLER_PATH set in .env.
    Returns an empty list and logs an error if Poppler is missing.
    """
    try:
        from pdf2image import convert_from_path
        from pdf2image.exceptions import (
            PDFInfoNotInstalledError,
            PDFPageCountError,
        )
    except ImportError:
        logger.error("pdf2image is not installed — cannot process PDFs.")
        return []

    poppler_path = config.POPPLER_PATH
    try:
        pil_images = convert_from_path(
            str(file_path),
            dpi=300,
            poppler_path=poppler_path,
        )
    except PDFInfoNotInstalledError:
        logger.error(
            "Poppler not found. Set POPPLER_PATH in .env or install Poppler. "
            "Cannot process PDF: %s", file_path.name,
        )
        return []
    except PDFPageCountError:
        logger.error("Could not read page count for PDF: %s", file_path.name)
        return []
    except Exception as exc:
        logger.error("Failed to convert PDF %s: %s", file_path.name, exc)
        return []

    return [np.array(img) for img in pil_images]


# ── OCR core ──────────────────────────────────────────────────

def _ocr_single_image(img: np.ndarray) -> str:
    """Preprocess one image and run Tesseract on it."""
    processed = preprocess_image(img)
    pil_img = Image.fromarray(processed)
    text = pytesseract.image_to_string(pil_img, lang="eng")
    return text.strip()


def _save_ocr_text(file_name: str, text: str) -> Path:
    """Write raw OCR text to ocr_output/ and return the path."""
    ocr_dir = config.OCR_OUTPUT_DIR
    ocr_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(file_name).stem
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = ocr_dir / f"{stem}_{timestamp}.txt"
    out_path.write_text(text, encoding="utf-8")
    return out_path


def extract_text(file_path: Path) -> dict:
    """
    Run OCR on an invoice file (image or PDF).

    Returns a dict with:
        success       — bool
        text          — extracted text (empty on failure)
        ocr_text_path — Path to saved raw text file (None on failure)
        error         — error message (empty on success)
    """
    file_name = file_path.name
    ext = file_path.suffix.lower()

    try:
        if ext == ".pdf":
            images = _pdf_to_images(file_path)
            if not images:
                return {
                    "success": False,
                    "text": "",
                    "ocr_text_path": None,
                    "error": "Failed to convert PDF to images (is Poppler installed?)",
                }
            # OCR each page, concatenate with page markers
            page_texts = []
            for i, img in enumerate(images, 1):
                page_text = _ocr_single_image(img)
                page_texts.append(f"--- Page {i} ---\n{page_text}")
            text = "\n\n".join(page_texts)

        else:
            # Image file — read via OpenCV
            img = cv2.imread(str(file_path))
            if img is None:
                return {
                    "success": False,
                    "text": "",
                    "ocr_text_path": None,
                    "error": f"OpenCV could not read image: {file_name}",
                }
            text = _ocr_single_image(img)

        # Save raw text
        ocr_text_path = _save_ocr_text(file_name, text)
        logger.info(
            "OCR complete for %s — %d chars extracted, saved to %s",
            file_name, len(text), ocr_text_path.name,
        )

        return {
            "success": True,
            "text": text,
            "ocr_text_path": ocr_text_path,
            "error": "",
        }

    except pytesseract.TesseractError as exc:
        error_msg = f"Tesseract error: {exc}"
        logger.error("OCR failed for %s: %s", file_name, error_msg)
        return {
            "success": False,
            "text": "",
            "ocr_text_path": None,
            "error": error_msg,
        }
    except Exception as exc:
        error_msg = f"Unexpected OCR error: {exc}"
        logger.error("OCR failed for %s: %s", file_name, error_msg)
        return {
            "success": False,
            "text": "",
            "ocr_text_path": None,
            "error": error_msg,
        }
