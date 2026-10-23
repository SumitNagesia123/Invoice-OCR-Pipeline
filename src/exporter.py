"""
InvoiceOCR Pipeline — Data Exporters.

Handles exporting processed invoice data to individual JSON files
and a consolidated Excel report.
"""

import json
import logging
import pandas as pd
from pathlib import Path
from src import config, storage, db

logger = logging.getLogger(__name__)

def export_to_json(document_id: int):
    """
    Export a single invoice to output/json/invoice_{id}.json
    """
    invoice = storage.get_invoice_by_id(document_id)
    if not invoice:
        return

    line_items = storage.get_line_items(document_id)
    invoice_data = dict(invoice)
    invoice_data["line_items"] = [dict(item) for item in line_items]

    output_dir = config.PROJECT_ROOT / "output" / "json"
    output_dir.mkdir(parents=True, exist_ok=True)

    file_path = output_dir / f"invoice_{document_id:03d}.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(invoice_data, f, indent=2, default=str)

    logger.info(f"Exported JSON for document {document_id} to {file_path}")

def export_to_excel():
    """
    Export all invoices to output/all_invoices.xlsx with three sheets.
    """
    conn = db.get_connection()

    # Read tables into pandas dataframes
    df_invoices = pd.read_sql_query("SELECT * FROM invoice_documents", conn)
    df_line_items = pd.read_sql_query("SELECT * FROM invoice_line_items", conn)
    df_needs_review = pd.read_sql_query("SELECT * FROM invoice_documents WHERE status = 'needs_review'", conn)

    conn.close()

    output_dir = config.PROJECT_ROOT / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    file_path = output_dir / "all_invoices.xlsx"

    with pd.ExcelWriter(file_path, engine='openpyxl') as writer:
        df_invoices.to_excel(writer, sheet_name='Invoices', index=False)
        df_line_items.to_excel(writer, sheet_name='Line Items', index=False)
        df_needs_review.to_excel(writer, sheet_name='Needs Review', index=False)

    logger.info(f"Exported Excel report to {file_path}")
