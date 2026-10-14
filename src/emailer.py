"""
InvoiceOCR Pipeline — Emailer.

Sends the daily status report via SMTP, including
aggregated statistics and the consolidated Excel report.
"""

import logging
import smtplib
from email.message import EmailMessage
from pathlib import Path
from src import config

logger = logging.getLogger(__name__)

def send_daily_summary(excel_path: Path, html_body: str, subject: str, dry_run: bool = False):
    """
    Composes and sends an email with the Excel attachment and HTML body.
    """
    if dry_run:
        logger.info("--- DRY RUN: Email Content ---")
        logger.info(f"Subject: {subject}")
        # logger.info(f"Body: {html_body}")
        logger.info(f"Attachment: {excel_path}")
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.SMTP_FROM
    msg["To"] = config.SMTP_TO
    msg.set_content("HTML report attached.") # Fallback
    msg.add_alternative(html_body, subtype="html")

    # Attach Excel
    with open(excel_path, "rb") as f:
        file_data = f.read()
        msg.add_attachment(file_data, maintype="application", subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=excel_path.name)

    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as server:
            server.starttls()
            if config.SMTP_USER and config.SMTP_PASSWORD:
                server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.send_message(msg)
        logger.info("Email summary sent.")
    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        # Save to output/last_report.html
        Path("output").mkdir(exist_ok=True)
        with open("output/last_report.html", "w") as f:
            f.write(html_body)
        logger.info("Saved HTML to output/last_report.html")
