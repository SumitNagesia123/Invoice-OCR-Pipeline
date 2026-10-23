"""
InvoiceOCR Pipeline — Comprehensive Reporter.

Generates:
  1. Excel report with multiple sheets.
  2. HTML report body for email.
"""

import logging
import warnings
import pandas as pd
from datetime import datetime
from src import config, db

logger = logging.getLogger(__name__)

# Suppress pandas warning about non-SQLAlchemy connectable
warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy connectable")

def generate_report_data(since, until):
    """Gather all data needed for reporting."""
    conn = db.get_connection()

    # Get all invoices, including line items sum
    invoices = pd.read_sql_query("""
        SELECT inv.*,
               (SELECT SUM(line_total) FROM invoice_line_items WHERE document_id = inv.document_id) as total_line_items_sum
        FROM invoice_documents inv
        WHERE inv.processed_at >= %s AND inv.processed_at <= %s
        ORDER BY inv.processed_at DESC
    """, conn, params=(since.isoformat(), until.isoformat()))

    # Get all line items for Excel
    line_items = pd.read_sql_query("""
        SELECT li.*
        FROM invoice_line_items li
        JOIN invoice_documents inv ON li.document_id = inv.document_id
        WHERE inv.processed_at >= %s AND inv.processed_at <= %s
    """, conn, params=(since.isoformat(), until.isoformat()))

    # Get all logs for run details
    logs = pd.read_sql_query("""
        SELECT * FROM processing_logs
        WHERE start_time >= %s AND start_time <= %s
    """, conn, params=(since.isoformat(), until.isoformat()))

    # Get duplicates for the current run period
    duplicates = pd.read_sql_query("""
        SELECT * FROM duplicate_files
        WHERE skipped_at >= %s AND skipped_at <= %s
        ORDER BY skipped_at DESC
    """, conn, params=(since.isoformat(), until.isoformat()))

    conn.close()

    return {
        "invoices": invoices,
        "line_items": line_items,
        "logs": logs,
        "duplicates_df": duplicates,
        "total": len(invoices) + len(duplicates),
        "success": len(invoices[invoices["status"] == "success"]),
        "needs_review": len(invoices[invoices["status"] == "needs_review"]),
        "failed": len(invoices[invoices["status"] == "failed"]),
        "duplicates": len(duplicates),
    }

def create_excel(data, output_path):
    """Create multi-sheet Excel file."""
    with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
        data["invoices"].to_excel(writer, sheet_name="invoices", index=False)
        data["line_items"].to_excel(writer, sheet_name="line_items", index=False)
        data["invoices"][data["invoices"]["status"] == "needs_review"].to_excel(writer, sheet_name="needs_review", index=False)
        data["invoices"][data["invoices"]["status"] == "failed"].to_excel(writer, sheet_name="failed", index=False)
        data["duplicates_df"].to_excel(writer, sheet_name="duplicates", index=False)

def create_html(data):
    """Create HTML report body."""
    total = data["total"]
    proc = data["success"]
    rev = data["needs_review"]
    fail = data["failed"]
    dups = data["duplicates"]

    check_sum = proc + rev + fail + dups
    check_ok = (check_sum == total)
    check_line = f"Check: {proc} + {rev} + {fail} + {dups} = {total} {'✔' if check_ok else '✘'}"

    mock_warning = ""
    if config.MOCK_EXTRACTOR:
        mock_warning = "<div style='background-color: #f8d7da; padding: 10px; color: #721c24;'>[MOCK MODE ACTIVE]</div>"

    html = f"""
    <html>
    <body style='font-family: sans-serif; line-height: 1.6;'>
        {mock_warning}
        <h2>Invoice Processing Report</h2>

        <h3>Run Overview</h3>
        <p>Total: {total}</p>
        <p style='color: {"green" if check_ok else "red"}'>{check_line}</p>

        <h3>Financial Summary</h3>
        <p>Total Invoiced Amount: {data['invoices']['total_amount'].sum():,.2f} USD</p>

        <h3>Needs Review</h3>
        {"<ul>" + "".join([f"<li>{r['file_name']}: {r['review_reason']}</li>" for _, r in data['invoices'][data['invoices']['status'] == 'needs_review'].head(20).iterrows()]) + "</ul>" if rev > 0 else "<p>None</p>"}

        <h3>Failed Files</h3>
        {"<ul>" + "".join([f"<li>{r['file_name']}: {r['failure_reason']}</li>" for _, r in data['invoices'][data['invoices']['status'] == 'failed'].head(20).iterrows()]) + "</ul>" if fail > 0 else "<p>None</p>"}

        <h3>Duplicates Skipped</h3>
        {"<ul>" + "".join([f"<li>{r['file_name']} (same as: {r['original_file']})</li>" for _, r in data['duplicates_df'].head(20).iterrows()]) + "</ul>" if dups > 0 else "<p>None</p>"}

        <h3>Run Details</h3>
        <p>Run mode: {'Mock' if config.MOCK_EXTRACTOR else 'Real'}</p>
        <p>Processed at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (IST)</p>
    </body>
    </html>
    """
    return html

def run_reporter(since, until):
    """Main reporting function."""
    data = generate_report_data(since, until)
    excel_path = config.PROJECT_ROOT / "output" / "all_invoices.xlsx"
    excel_path.parent.mkdir(exist_ok=True)
    create_excel(data, excel_path)
    html = create_html(data)

    return excel_path, html
