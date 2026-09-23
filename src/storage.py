"""
InvoiceOCR Pipeline — Storage.

Takes structured data from the parser (and metadata from intake/OCR) and
writes it to the SQLite database.  Responsible for:

  - Field validation / sanitization before insert
  - Inserting into invoice_documents + invoice_line_items
  - Updating processing_logs with the outcome
  - Moving processed files to processed_invoices/
"""

import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src import config
from src.db import get_connection

logger = logging.getLogger(__name__)


# ── Field validation ─────────────────────────────────────────

_MAX_INVOICE_NUMBER_LEN = 50
_MAX_VENDOR_NAME_LEN = 200
_MAX_DESCRIPTION_LEN = 200
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


def _validate_invoice_number(value: str | None) -> str | None:
    """Validate and sanitize the invoice number."""
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return None
    return value[:_MAX_INVOICE_NUMBER_LEN]


def _validate_date(value: str | None) -> str | None:
    """Validate the invoice date is a well-formed YYYY-MM-DD string."""
    if value is None:
        return None
    value = str(value).strip()
    if not _ISO_DATE_RE.match(value):
        logger.warning("Invalid date format, discarding: %r", value)
        return None
    # Verify it's a real date
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        logger.warning("Invalid calendar date, discarding: %r", value)
        return None
    return value


def _validate_vendor_name(value: str | None) -> str | None:
    """Validate and sanitize the vendor name."""
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return None
    return value[:_MAX_VENDOR_NAME_LEN]


def _validate_amount(value: float | None) -> float | None:
    """Validate the total amount is a non-negative number."""
    if value is None:
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError):
        logger.warning("Invalid amount, discarding: %r", value)
        return None
    if amount < 0:
        logger.warning("Negative amount, discarding: %.2f", amount)
        return None
    return round(amount, 2)


def _validate_currency(value: str | None) -> str | None:
    """Validate the currency is a 3-letter ISO code."""
    if value is None:
        return None
    value = str(value).strip().upper()
    if not _CURRENCY_RE.match(value):
        logger.warning("Invalid currency code, discarding: %r", value)
        return None
    return value


def _validate_line_item(item: dict[str, Any]) -> dict[str, Any] | None:
    """
    Validate a single line item dict.

    Returns a sanitized dict, or None if the item is not usable
    (missing description entirely).
    """
    desc = item.get("description")
    if desc is None or not str(desc).strip():
        return None
    desc = str(desc).strip()[:_MAX_DESCRIPTION_LEN]

    qty = item.get("quantity")
    if qty is not None:
        try:
            qty = float(qty)
            if qty < 0:
                qty = None
        except (TypeError, ValueError):
            qty = None

    unit_price = item.get("unit_price")
    if unit_price is not None:
        try:
            unit_price = round(float(unit_price), 2)
            if unit_price < 0:
                unit_price = None
        except (TypeError, ValueError):
            unit_price = None

    line_total = item.get("line_total")
    if line_total is not None:
        try:
            line_total = round(float(line_total), 2)
            if line_total < 0:
                line_total = None
        except (TypeError, ValueError):
            line_total = None

    return {
        "description": desc,
        "quantity": qty,
        "unit_price": unit_price,
        "line_total": line_total,
    }


def validate_parsed_data(parsed: dict[str, Any]) -> dict[str, Any]:
    """
    Validate all parsed fields before database insertion.

    Returns a new dict with sanitized values.  Invalid fields become None.
    """
    validated_items = []
    for item in parsed.get("line_items", []):
        v = _validate_line_item(item)
        if v is not None:
            validated_items.append(v)

    return {
        "invoice_number": _validate_invoice_number(parsed.get("invoice_number")),
        "invoice_date": _validate_date(parsed.get("invoice_date")),
        "vendor_name": _validate_vendor_name(parsed.get("vendor_name")),
        "total_amount": _validate_amount(parsed.get("total_amount")),
        "currency": _validate_currency(parsed.get("currency")),
        "line_items": validated_items,
        "confidence": parsed.get("confidence", "low"),
    }


# ── Database insertion ───────────────────────────────────────

def store_invoice(
    file_name: str,
    file_hash: str,
    ocr_text_path: str | Path | None,
    parsed: dict[str, Any],
) -> int:
    """
    Insert a fully-parsed invoice into the database.

    Validates all fields before inserting.  Creates a row in
    invoice_documents and one row per line item in invoice_line_items.

    Args:
        file_name:     Sanitized filename from intake.
        file_hash:     SHA-256 hex digest from intake.
        ocr_text_path: Path to the saved OCR text file (may be None).
        parsed:        Dict from parse_invoice_text().

    Returns:
        The document_id of the inserted invoice_documents row.
    """
    validated = validate_parsed_data(parsed)

    # Determine status based on confidence
    confidence = validated["confidence"]
    if confidence == "high":
        status = "processed"
    else:
        status = "needs_review"

    ocr_path_str = str(ocr_text_path) if ocr_text_path else None

    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO invoice_documents
                (file_name, file_hash, invoice_number, invoice_date,
                 vendor_name, total_amount, currency, status,
                 ocr_text_path, processed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                file_name,
                file_hash,
                validated["invoice_number"],
                validated["invoice_date"],
                validated["vendor_name"],
                validated["total_amount"],
                validated["currency"],
                status,
                ocr_path_str,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        document_id = cur.lastrowid

        # Insert line items
        for item in validated["line_items"]:
            conn.execute(
                """
                INSERT INTO invoice_line_items
                    (document_id, description, quantity, unit_price, line_total)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    item["description"],
                    item["quantity"],
                    item["unit_price"],
                    item["line_total"],
                ),
            )

        conn.commit()
        logger.info(
            "Stored invoice document_id=%d — %s (%s, %d line items)",
            document_id, file_name, status, len(validated["line_items"]),
        )
        return document_id

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Processing-log update ────────────────────────────────────

def finalize_processing_log(
    log_id: int,
    success: bool,
    error_message: str | None = None,
) -> None:
    """
    Mark a processing_logs row as finished (success or failed).

    Called after OCR + parse + store are complete (or after a failure).
    """
    status = "success" if success else "failed"
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE processing_logs
            SET end_time = ?, status = ?, error_message = ?
            WHERE log_id = ?
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                status,
                error_message,
                log_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


# ── File routing ─────────────────────────────────────────────

def move_to_processed(file_path: Path) -> Path:
    """
    Move a successfully-processed file to processed_invoices/.

    Returns the destination path.
    """
    processed_dir = config.PROCESSED_DIR
    processed_dir.mkdir(parents=True, exist_ok=True)
    dest = processed_dir / file_path.name
    # Avoid overwriting
    if dest.exists():
        stem = dest.stem
        ext = dest.suffix
        counter = 1
        while dest.exists():
            dest = processed_dir / f"{stem}_{counter}{ext}"
            counter += 1
    shutil.move(str(file_path), str(dest))
    logger.info("Moved to processed: %s → %s", file_path.name, dest.name)
    return dest


# ── Query helpers ────────────────────────────────────────────

def get_invoice_by_id(document_id: int) -> dict[str, Any] | None:
    """Retrieve an invoice_documents row by its ID."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM invoice_documents WHERE document_id = ?",
            (document_id,),
        ).fetchone()
        if row is None:
            return None
        return dict(row)
    finally:
        conn.close()


def get_line_items(document_id: int) -> list[dict[str, Any]]:
    """Retrieve all line items for a given document_id."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM invoice_line_items WHERE document_id = ?",
            (document_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_processing_log(log_id: int) -> dict[str, Any] | None:
    """Retrieve a processing_logs row by its ID."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM processing_logs WHERE log_id = ?",
            (log_id,),
        ).fetchone()
        if row is None:
            return None
        return dict(row)
    finally:
        conn.close()
