"""
InvoiceOCR Pipeline — Reporting.

Generates a daily email summary of pipeline activity and sends it via
SMTP.  Queries invoice_documents and processing_logs for a given date
range (default: last 24 hours).

The report includes:
  - Total invoices processed vs. needing review vs. failed
  - Per-invoice details (file name, vendor, amount, status)
  - Processing errors (from processing_logs)

All SMTP credentials come from ``src.config`` (backed by ``.env``).
"""

import logging
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any

from src import config
from src.db import get_connection

logger = logging.getLogger(__name__)


# ── Data gathering ──────────────────────────────────────────

def get_summary_data(
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict[str, Any]:
    """
    Query the database for invoices and processing logs within a window.

    Args:
        since: Start of the window (inclusive).  Defaults to 24 h ago.
        until: End of the window (inclusive).  Defaults to now.

    Returns a dict with keys:
        invoices      — list of invoice_documents rows (as dicts)
        logs          — list of processing_logs rows (as dicts)
        total         — total invoices in the window
        processed     — count with status = 'processed'
        needs_review  — count with status = 'needs_review'
        failed_logs   — count of processing_logs with status = 'failed'
    """
    now = datetime.now(timezone.utc)
    if until is None:
        until = now
    if since is None:
        since = until - timedelta(hours=24)

    since_str = since.isoformat()
    until_str = until.isoformat()

    conn = get_connection()
    try:
        invoices = [
            dict(r) for r in conn.execute(
                """
                SELECT * FROM invoice_documents
                WHERE processed_at >= ? AND processed_at <= ?
                ORDER BY processed_at DESC
                """,
                (since_str, until_str),
            ).fetchall()
        ]

        logs = [
            dict(r) for r in conn.execute(
                """
                SELECT * FROM processing_logs
                WHERE start_time >= ? AND start_time <= ?
                ORDER BY start_time DESC
                """,
                (since_str, until_str),
            ).fetchall()
        ]
    finally:
        conn.close()

    processed = sum(1 for inv in invoices if inv["status"] == "processed")
    needs_review = sum(1 for inv in invoices if inv["status"] == "needs_review")
    failed_logs = sum(1 for log in logs if log["status"] == "failed")

    return {
        "invoices": invoices,
        "logs": logs,
        "total": len(invoices),
        "processed": processed,
        "needs_review": needs_review,
        "failed_logs": failed_logs,
        "since": since,
        "until": until,
    }


# ── Email composition ───────────────────────────────────────

def build_report_body(data: dict[str, Any]) -> str:
    """
    Build a plain-text email body from summary data.

    The body includes a header with date range, a statistics overview, a
    per-invoice table, and a list of processing errors (if any).
    """
    since_fmt = data["since"].strftime("%Y-%m-%d %H:%M UTC")
    until_fmt = data["until"].strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = []
    lines.append("InvoiceOCR Pipeline — Daily Summary")
    lines.append("=" * 40)
    lines.append(f"Period: {since_fmt}  →  {until_fmt}")
    lines.append("")

    # ── Statistics ──
    lines.append("Statistics")
    lines.append("-" * 20)
    lines.append(f"  Total invoices:   {data['total']}")
    lines.append(f"  Processed (auto): {data['processed']}")
    lines.append(f"  Needs review:     {data['needs_review']}")
    lines.append(f"  Failed jobs:      {data['failed_logs']}")
    lines.append("")

    # ── Invoice details ──
    invoices = data["invoices"]
    if invoices:
        lines.append("Invoices")
        lines.append("-" * 20)
        for inv in invoices:
            vendor = inv.get("vendor_name") or "(unknown)"
            amount = inv.get("total_amount")
            currency = inv.get("currency") or ""
            amount_str = f"{currency} {amount:.2f}" if amount is not None else "N/A"
            status = inv.get("status", "")
            lines.append(
                f"  {inv['file_name']:<30} {vendor:<25} "
                f"{amount_str:<15} [{status}]"
            )
        lines.append("")
    else:
        lines.append("No invoices processed in this period.")
        lines.append("")

    # ── Errors ──
    failed = [log for log in data["logs"] if log["status"] == "failed"]
    if failed:
        lines.append("Errors")
        lines.append("-" * 20)
        for log in failed:
            msg = log.get("error_message") or "(no message)"
            lines.append(f"  {log['file_name']}: {msg}")
        lines.append("")

    lines.append("— End of report —")
    return "\n".join(lines)


def build_report_subject(data: dict[str, Any]) -> str:
    """Build the email subject line from summary statistics."""
    date_str = data["until"].strftime("%Y-%m-%d")
    return (
        f"InvoiceOCR Summary {date_str} — "
        f"{data['total']} invoices, "
        f"{data['failed_logs']} errors"
    )


# ── Email sending ───────────────────────────────────────────

def _validate_smtp_config() -> list[str]:
    """
    Check that all required SMTP settings are configured.

    Returns a list of missing field names (empty = all good).
    """
    required = {
        "SMTP_HOST": config.SMTP_HOST,
        "SMTP_FROM": config.SMTP_FROM,
        "SMTP_TO": config.SMTP_TO,
    }
    return [k for k, v in required.items() if not v]


def send_report(
    subject: str,
    body: str,
) -> dict[str, Any]:
    """
    Send the summary email via SMTP.

    Uses TLS (STARTTLS) on the configured port.  Authenticates only when
    both SMTP_USER and SMTP_PASSWORD are set (some relays don't require
    auth).

    Returns a dict:
        success — bool
        error   — str (empty on success)
    """
    missing = _validate_smtp_config()
    if missing:
        error_msg = f"SMTP not configured — missing: {', '.join(missing)}"
        logger.error(error_msg)
        return {"success": False, "error": error_msg}

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.SMTP_FROM
    msg["To"] = config.SMTP_TO
    msg.set_content(body)

    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT,
                          timeout=30) as server:
            server.starttls()
            if config.SMTP_USER and config.SMTP_PASSWORD:
                server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.send_message(msg)

        logger.info("Summary email sent to %s", config.SMTP_TO)
        return {"success": True, "error": ""}

    except smtplib.SMTPException as exc:
        error_msg = f"SMTP error: {exc}"
        logger.error(error_msg)
        return {"success": False, "error": error_msg}
    except OSError as exc:
        error_msg = f"Connection error: {exc}"
        logger.error(error_msg)
        return {"success": False, "error": error_msg}


# ── Convenience wrapper ────────────────────────────────────

def generate_and_send_report(
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict[str, Any]:
    """
    End-to-end: gather data → compose email → send.

    Returns a dict with:
        success  — bool
        error    — str (empty on success)
        subject  — the email subject used
        body     — the email body sent
        data     — the raw summary data dict
    """
    data = get_summary_data(since=since, until=until)
    body = build_report_body(data)
    subject = build_report_subject(data)

    result = send_report(subject, body)

    return {
        "success": result["success"],
        "error": result["error"],
        "subject": subject,
        "body": body,
        "data": data,
    }
