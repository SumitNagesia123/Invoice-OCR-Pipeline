"""
InvoiceOCR Pipeline — File intake.

Scans the input folder for new invoice files, validates them,
detects duplicates via SHA-256 hash, and routes unreadable
or unsupported files to the failed folder with a logged reason.
"""

import hashlib
import logging
import re
import shutil
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

IST = ZoneInfo("Asia/Kolkata")

from PIL import Image

from src import config, storage
from src.db import get_connection, row_to_dict

logger = logging.getLogger(__name__)


def compute_file_hash(file_path: Path) -> str:
    """Return the SHA-256 hex digest of a file's contents."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def sanitize_filename(name: str) -> str:
    """
    Remove or replace characters that could cause path-traversal
    or filesystem issues.  Preserves the extension.
    This does NOT rename the original file; it returns a safe version
    for use in output paths, appending a counter if a collision occurs.
    """
    name = name.replace("/", "_").replace("\\", "_").replace("\0", "")
    name = re.sub(r"\.{2,}", ".", name)
    name = name.lstrip(".")
    name = re.sub(r"[^\w\-. ]", "_", name)
    return name or "unnamed_file"


def _is_valid_image(file_path: Path) -> bool:
    """Return True if Pillow can open and verify the image."""
    try:
        with Image.open(file_path) as img:
            img.verify()
        return True
    except Exception:
        return False


def _is_valid_pdf(file_path: Path) -> bool:
    """Return True if the file starts with the PDF magic bytes."""
    try:
        with open(file_path, "rb") as f:
            header = f.read(5)
        return header == b"%PDF-"
    except Exception:
        return False


def is_file_valid(file_path: Path) -> tuple[bool, str]:
    """
    Check whether a file is a supported, non-corrupt invoice file.

    Returns (ok, reason).  `reason` is empty on success.
    """
    if not file_path.exists():
        return False, "File does not exist"

    ext = file_path.suffix.lower()

    if ext not in config.SUPPORTED_EXTENSIONS:
        return False, f"Unsupported file type: {ext}"

    if file_path.stat().st_size == 0:
        return False, "File is empty (0 bytes)"

    if ext == ".pdf":
        if not _is_valid_pdf(file_path):
            return False, "File does not appear to be a valid PDF"
    else:
        if not _is_valid_image(file_path):
            return False, "File is not a readable image"

    return True, ""


def get_original_file_name_by_hash(file_hash: str) -> str | None:
    """Return the filename for a given file_hash from successful/reviewed documents."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT file_name FROM invoice_documents WHERE file_hash = %s AND status != 'failed' LIMIT 1", (file_hash,))
        row = cursor.fetchone()
        return row[0] if row else None
    finally:
        cursor.close()
        conn.close()

def is_duplicate(file_hash: str) -> bool:
    """Return True if a non-failed document with this hash is already in the DB."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT 1 FROM invoice_documents WHERE file_hash = %s AND status != 'failed'",
            (file_hash,),
        )
        row = cursor.fetchone()
        return row is not None
    finally:
        cursor.close()
        conn.close()


def get_active_document_by_filename(file_name: str) -> dict | None:
    """Return dict of file_hash and status for non-failed document with this filename, or None."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT file_hash, status FROM invoice_documents WHERE file_name = %s AND status != 'failed' LIMIT 1",
            (file_name,),
        )
        row = cursor.fetchone()
        return row_to_dict(cursor, row) if row else None
    finally:
        cursor.close()
        conn.close()


def move_to_failed(file_path: Path, reason: str) -> Path:
    """Move a file to the failed_invoices folder and log the reason."""
    failed_dir = config.FAILED_DIR
    failed_dir.mkdir(parents=True, exist_ok=True)
    dest = failed_dir / file_path.name
    if dest.exists():
        stem = dest.stem
        ext = dest.suffix
        counter = 1
        while dest.exists():
            dest = failed_dir / f"{stem}_{counter}{ext}"
            counter += 1
    shutil.move(str(file_path), str(dest))
    logger.warning("Moved to failed: %s — %s", file_path.name, reason)
    return dest


def _log_processing_start(file_name: str) -> int:
    """Insert a processing_logs row and return its log_id."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO processing_logs (file_name, start_time, status) "
            "VALUES (%s, %s, 'running')",
            (file_name, datetime.now(IST).isoformat()),
        )
        log_id = cursor.lastrowid
        conn.commit()
        return log_id
    finally:
        cursor.close()
        conn.close()


def _log_processing_end(log_id: int, status: str,
                        error_message: str | None = None) -> None:
    """Update a processing_logs row with the outcome."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE processing_logs "
            "SET end_time = %s, status = %s, error_message = %s "
            "WHERE log_id = %s",
            (datetime.now(IST).isoformat(), status, error_message,
             log_id),
        )
        conn.commit()
    finally:
        cursor.close()
        conn.close()


def scan_input_folder() -> list[dict]:
    input_dir = config.INPUT_DIR
    input_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []

    files = sorted(
        f for f in input_dir.rglob("*")
        if f.is_file() and f.name != ".gitkeep"
    )

    if not files:
        logger.info("No files found in %s", input_dir)
        return results

    logger.info("Found %d file(s) in %s", len(files), input_dir)

    seen_hashes: set[str] = set()
    seen_filenames: set[tuple[str, str]] = set()

    for file_path in files:
        original_name = file_path.name
        log_id = _log_processing_start(original_name)

        if file_path.exists() and file_path.stat().st_size == 0:
            file_hash = None
        else:
            try:
                file_hash = compute_file_hash(file_path)
            except Exception:
                file_hash = None

        ok, reason = is_file_valid(file_path)
        if not ok:
            storage.store_failed_invoice(original_name, file_hash, reason)
            move_to_failed(file_path, reason)
            _log_processing_end(log_id, "failed", reason)
            results.append({
                "file_path": file_path,
                "file_name": original_name,
                "file_hash": file_hash,
                "log_id": log_id,
                "status": "failed",
                "reason": reason,
            })
            continue

        # Valid file but hash computation failed — treat as failed
        if file_hash is None:
            reason = "Could not compute file hash"
            storage.store_failed_invoice(original_name, None, reason)
            move_to_failed(file_path, reason)
            _log_processing_end(log_id, "failed", reason)
            results.append({
                "file_path": file_path,
                "file_name": original_name,
                "file_hash": None,
                "log_id": log_id,
                "status": "failed",
                "reason": reason,
            })
            continue

        if file_hash in seen_hashes:
            reason = "Duplicate file (hash already seen in current batch)"
            original_file = next((fn for fn, fh in seen_filenames if fh == file_hash), "unknown")
            storage.store_duplicate(original_name, file_hash, original_file)
            try:
                storage.move_to_processed(file_path)
            except Exception:
                file_path.unlink(missing_ok=True)
            _log_processing_end(log_id, "duplicate_skipped", reason)
            results.append({
                "file_path": file_path,
                "file_name": original_name,
                "file_hash": file_hash,
                "log_id": log_id,
                "status": "duplicate_skipped",
                "reason": reason,
            })
            continue

        if is_duplicate(file_hash):
            reason = "Duplicate file (hash already in database)"
            original_file = get_original_file_name_by_hash(file_hash)
            storage.store_duplicate(original_name, file_hash, original_file or "unknown")
            try:
                storage.move_to_processed(file_path)
            except Exception:
                file_path.unlink(missing_ok=True)
            _log_processing_end(log_id, "duplicate_skipped", reason)
            results.append({
                "file_path": file_path,
                "file_name": original_name,
                "file_hash": file_hash,
                "log_id": log_id,
                "status": "duplicate_skipped",
                "reason": reason,
            })
            continue

        # Filename check against database (active / non-failed documents)
        existing_doc = get_active_document_by_filename(original_name)
        if existing_doc:
            existing_hash = existing_doc.get("file_hash")
            if existing_hash == file_hash:
                reason = "Duplicate file (same filename and hash already in database)"
                storage.store_duplicate(original_name, file_hash, original_name)
                try:
                    storage.move_to_processed(file_path)
                except Exception:
                    file_path.unlink(missing_ok=True)
                _log_processing_end(log_id, "duplicate_skipped", reason)
                results.append({
                    "file_path": file_path,
                    "file_name": original_name,
                    "file_hash": file_hash,
                    "log_id": log_id,
                    "status": "duplicate_skipped",
                    "reason": reason,
                })
                seen_hashes.add(file_hash)
                seen_filenames.add((original_name, file_hash))
                continue
            else:
                # Same filename + different hash = store under unique name
                stem = file_path.stem
                ext = file_path.suffix
                original_name = f"{stem}_{file_hash[:8]}{ext}"
                logger.info("Filename collision with different hash; storing as %s", original_name)

        seen_hashes.add(file_hash)
        seen_filenames.add((original_name, file_hash))

        logger.info("Ready for OCR: %s (hash: %s…)", original_name, file_hash[:12])
        results.append({
            "file_path": file_path,
            "file_name": original_name,
            "file_hash": file_hash,
            "log_id": log_id,
            "status": "ready",
            "reason": "",
        })

    return results
