"""
InvoiceOCR Pipeline — Storage.

Takes structured data from the parser (and metadata from intake/OCR) and
writes it to the MySQL database.  Responsible for:

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
from src.db import get_connection, row_to_dict

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
    review_reason: str | None = None,
    failure_reason: str | None = None,
    original_file: str | None = None,
) -> int:
    """
    Insert a fully-parsed invoice into the database.
    """
    validated = validate_parsed_data(parsed)

    if failure_reason:
        status = "failed"
    else:
        confidence = validated["confidence"]
        if confidence == "high":
            status = "success"
        else:
            status = "needs_review"

    if ocr_text_path:
        ocr_path = Path(ocr_text_path)
        try:
            ocr_path = ocr_path.relative_to(config.PROJECT_ROOT)
        except ValueError:
            pass
        ocr_path_str = str(ocr_path)
    else:
        ocr_path_str = None

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO invoice_documents
                (file_name, file_hash, invoice_number, invoice_date,
                 vendor_name, total_amount, currency, status,
                 ocr_text_path, review_reason, failure_reason, original_file)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                invoice_number = VALUES(invoice_number),
                invoice_date = VALUES(invoice_date),
                vendor_name = VALUES(vendor_name),
                total_amount = VALUES(total_amount),
                currency = VALUES(currency),
                status = VALUES(status),
                ocr_text_path = VALUES(ocr_text_path),
                review_reason = VALUES(review_reason),
                failure_reason = NULL,
                original_file = VALUES(original_file)
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
                review_reason,
                failure_reason,
                original_file,
            ),
        )
        document_id = cursor.lastrowid
        if document_id == 0:
            cursor.execute("SELECT document_id FROM invoice_documents WHERE file_name = %s", (file_name,))
            document_id = cursor.fetchone()[0]

        for item in validated["line_items"]:
            cursor.execute(
                """
                INSERT INTO invoice_line_items
                    (document_id, description, quantity, unit_price, line_total)
                VALUES (%s, %s, %s, %s, %s)
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
        cursor.close()
        conn.close()


def store_failed_invoice(file_name: str, file_hash: str | None, failure_reason: str) -> None:
    """
    Store or update a failed invoice record, allowing retries.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO invoice_documents
                (file_name, file_hash, status, failure_reason)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                status = 'failed',
                failure_reason = VALUES(failure_reason),
                file_hash = VALUES(file_hash)
        """, (file_name, file_hash, 'failed', failure_reason))
        conn.commit()
    finally:
        cursor.close()
        conn.close()


def store_duplicate(file_name: str, file_hash: str, original_file: str) -> None:
    """
    Record that a duplicate invoice file was skipped in duplicate_files.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO duplicate_files
                (file_name, file_hash, original_file)
            VALUES (%s, %s, %s)
        """, (file_name, file_hash, original_file))
        conn.commit()
    finally:
        cursor.close()
        conn.close()


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
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE processing_logs
            SET end_time = CURRENT_TIMESTAMP, status = %s, error_message = %s
            WHERE log_id = %s
            """,
            (
                status,
                error_message,
                log_id,
            ),
        )
        conn.commit()
    finally:
        cursor.close()
        conn.close()


def move_to_processed(file_path: Path) -> Path:
    """
    Move a successfully-processed file to processed_invoices/.

    Returns the destination path.
    """
    processed_dir = config.PROCESSED_DIR
    processed_dir.mkdir(parents=True, exist_ok=True)
    dest = processed_dir / file_path.name
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


def get_invoice_by_id(document_id: int) -> dict[str, Any] | None:
    """Retrieve an invoice_documents row by its ID."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT * FROM invoice_documents WHERE document_id = %s",
            (document_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return row_to_dict(cursor, row)
    finally:
        cursor.close()
        conn.close()


def get_line_items(document_id: int) -> list[dict[str, Any]]:
    """Retrieve all line items for a given document_id."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT * FROM invoice_line_items WHERE document_id = %s",
            (document_id,),
        )
        rows = cursor.fetchall()
        return [row_to_dict(cursor, r) for r in rows]
    finally:
        cursor.close()
        conn.close()


def get_processing_log(log_id: int) -> dict[str, Any] | None:
    """Retrieve a processing_logs row by its ID."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT * FROM processing_logs WHERE log_id = %s",
            (log_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return row_to_dict(cursor, row)
    finally:
        cursor.close()
        conn.close()
