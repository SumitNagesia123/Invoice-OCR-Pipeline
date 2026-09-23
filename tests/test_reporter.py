"""
Tests for the reporting module.

Covers:
  - get_summary_data: window defaults, filtering, status counts
  - build_report_body: content sections, empty data handling
  - build_report_subject: format and statistics
  - _validate_smtp_config: missing-field detection
  - send_report: happy path (mocked SMTP), auth skipping, SMTP errors,
    connection errors, missing config
  - generate_and_send_report: end-to-end wiring
"""

import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config
from src.db import init_db, get_connection
from src.storage import store_invoice
from src.reporter import (
    get_summary_data,
    build_report_body,
    build_report_subject,
    _validate_smtp_config,
    send_report,
    generate_and_send_report,
)


class _TempDBTestCase(unittest.TestCase):
    """Redirect DB and folders to a temp directory for isolation."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.data_dir = self.tmpdir / "data"
        self.processed_dir = self.tmpdir / "processed_invoices"
        self.input_dir = self.tmpdir / "input_invoices"

        for d in (self.data_dir, self.processed_dir, self.input_dir):
            d.mkdir()

        self.db_path = self.data_dir / "test.db"

        self._patches = [
            patch.object(config, "DATABASE_PATH", self.db_path),
            patch.object(config, "PROCESSED_DIR", self.processed_dir),
            patch.object(config, "INPUT_DIR", self.input_dir),
        ]
        for p in self._patches:
            p.start()

        init_db()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @staticmethod
    def _make_parsed(
        invoice_number="INV-001",
        invoice_date="2024-01-15",
        vendor_name="Acme Corp",
        total_amount=1000.00,
        currency="USD",
        line_items=None,
        confidence="high",
    ) -> dict:
        if line_items is None:
            line_items = [
                {
                    "description": "Widget",
                    "quantity": 5,
                    "unit_price": 100.00,
                    "line_total": 500.00,
                },
            ]
        return {
            "invoice_number": invoice_number,
            "invoice_date": invoice_date,
            "vendor_name": vendor_name,
            "total_amount": total_amount,
            "currency": currency,
            "line_items": line_items,
            "confidence": confidence,
        }

    def _insert_processing_log(
        self, file_name: str = "test.png",
        status: str = "success",
        error_message: str | None = None,
    ) -> int:
        conn = get_connection()
        try:
            cur = conn.execute(
                "INSERT INTO processing_logs "
                "(file_name, start_time, end_time, status, error_message) "
                "VALUES (?, datetime('now'), datetime('now'), ?, ?)",
                (file_name, status, error_message),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()


# ── get_summary_data ────────────────────────────────────────

class TestGetSummaryData(_TempDBTestCase):

    def test_empty_database(self):
        data = get_summary_data()
        self.assertEqual(data["total"], 0)
        self.assertEqual(data["processed"], 0)
        self.assertEqual(data["needs_review"], 0)
        self.assertEqual(data["failed_logs"], 0)
        self.assertEqual(data["invoices"], [])
        self.assertEqual(data["logs"], [])

    def test_includes_recent_invoices(self):
        store_invoice("inv1.png", "hash1", None, self._make_parsed(
            invoice_number="INV-001", confidence="high"))
        store_invoice("inv2.png", "hash2", None, self._make_parsed(
            invoice_number="INV-002", confidence="medium"))

        data = get_summary_data()
        self.assertEqual(data["total"], 2)
        self.assertEqual(data["processed"], 1)
        self.assertEqual(data["needs_review"], 1)

    def test_excludes_old_invoices(self):
        store_invoice("old.png", "hash_old", None, self._make_parsed())
        # Move processed_at to 2 days ago
        conn = get_connection()
        try:
            two_days_ago = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
            conn.execute(
                "UPDATE invoice_documents SET processed_at = ? "
                "WHERE file_name = 'old.png'",
                (two_days_ago,),
            )
            conn.commit()
        finally:
            conn.close()

        data = get_summary_data()
        self.assertEqual(data["total"], 0)

    def test_custom_window(self):
        store_invoice("win.png", "hash_win", None, self._make_parsed())
        now = datetime.now(timezone.utc)
        data = get_summary_data(
            since=now - timedelta(minutes=5),
            until=now + timedelta(minutes=5),
        )
        self.assertEqual(data["total"], 1)

    def test_counts_failed_logs(self):
        self._insert_processing_log("fail1.png", status="failed",
                                    error_message="OCR timed out")
        self._insert_processing_log("ok1.png", status="success")

        data = get_summary_data()
        self.assertEqual(data["failed_logs"], 1)
        self.assertEqual(len(data["logs"]), 2)

    def test_since_and_until_in_result(self):
        data = get_summary_data()
        self.assertIsInstance(data["since"], datetime)
        self.assertIsInstance(data["until"], datetime)


# ── build_report_body ───────────────────────────────────────

class TestBuildReportBody(unittest.TestCase):

    def _make_data(self, invoices=None, logs=None, **overrides):
        now = datetime.now(timezone.utc)
        defaults = {
            "invoices": invoices or [],
            "logs": logs or [],
            "total": len(invoices or []),
            "processed": 0,
            "needs_review": 0,
            "failed_logs": 0,
            "since": now - timedelta(hours=24),
            "until": now,
        }
        defaults.update(overrides)
        return defaults

    def test_header_present(self):
        data = self._make_data()
        body = build_report_body(data)
        self.assertIn("InvoiceOCR Pipeline", body)
        self.assertIn("Daily Summary", body)

    def test_statistics_section(self):
        data = self._make_data(total=5, processed=3,
                               needs_review=2, failed_logs=1)
        body = build_report_body(data)
        self.assertIn("Total invoices:   5", body)
        self.assertIn("Processed (auto): 3", body)
        self.assertIn("Needs review:     2", body)
        self.assertIn("Failed jobs:      1", body)

    def test_invoice_details_listed(self):
        invoices = [
            {"file_name": "inv1.png", "vendor_name": "Acme",
             "total_amount": 500.0, "currency": "USD",
             "status": "processed"},
        ]
        data = self._make_data(invoices=invoices, total=1, processed=1)
        body = build_report_body(data)
        self.assertIn("inv1.png", body)
        self.assertIn("Acme", body)
        self.assertIn("USD 500.00", body)
        self.assertIn("[processed]", body)

    def test_invoice_missing_vendor(self):
        invoices = [
            {"file_name": "no_vendor.png", "vendor_name": None,
             "total_amount": None, "currency": None,
             "status": "needs_review"},
        ]
        data = self._make_data(invoices=invoices, total=1, needs_review=1)
        body = build_report_body(data)
        self.assertIn("(unknown)", body)
        self.assertIn("N/A", body)

    def test_no_invoices_message(self):
        data = self._make_data()
        body = build_report_body(data)
        self.assertIn("No invoices processed in this period", body)

    def test_errors_section(self):
        logs = [
            {"file_name": "bad.png", "status": "failed",
             "error_message": "OCR timed out"},
        ]
        data = self._make_data(logs=logs, failed_logs=1)
        body = build_report_body(data)
        self.assertIn("Errors", body)
        self.assertIn("bad.png", body)
        self.assertIn("OCR timed out", body)

    def test_error_without_message(self):
        logs = [
            {"file_name": "bad2.png", "status": "failed",
             "error_message": None},
        ]
        data = self._make_data(logs=logs, failed_logs=1)
        body = build_report_body(data)
        self.assertIn("(no message)", body)

    def test_no_errors_section_when_none(self):
        logs = [
            {"file_name": "ok.png", "status": "success",
             "error_message": None},
        ]
        data = self._make_data(logs=logs)
        body = build_report_body(data)
        self.assertNotIn("Errors", body)

    def test_end_marker(self):
        data = self._make_data()
        body = build_report_body(data)
        self.assertIn("End of report", body)


# ── build_report_subject ────────────────────────────────────

class TestBuildReportSubject(unittest.TestCase):

    def test_format(self):
        now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        data = {
            "total": 7,
            "failed_logs": 2,
            "until": now,
        }
        subject = build_report_subject(data)
        self.assertEqual(
            subject,
            "InvoiceOCR Summary 2024-06-15 — 7 invoices, 2 errors",
        )

    def test_zero_counts(self):
        now = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        data = {"total": 0, "failed_logs": 0, "until": now}
        subject = build_report_subject(data)
        self.assertIn("0 invoices", subject)
        self.assertIn("0 errors", subject)


# ── _validate_smtp_config ───────────────────────────────────

class TestValidateSmtpConfig(unittest.TestCase):

    def test_all_present(self):
        with patch.object(config, "SMTP_HOST", "smtp.example.com"), \
             patch.object(config, "SMTP_FROM", "a@b.com"), \
             patch.object(config, "SMTP_TO", "c@d.com"):
            self.assertEqual(_validate_smtp_config(), [])

    def test_missing_host(self):
        with patch.object(config, "SMTP_HOST", ""), \
             patch.object(config, "SMTP_FROM", "a@b.com"), \
             patch.object(config, "SMTP_TO", "c@d.com"):
            missing = _validate_smtp_config()
            self.assertIn("SMTP_HOST", missing)

    def test_missing_all(self):
        with patch.object(config, "SMTP_HOST", ""), \
             patch.object(config, "SMTP_FROM", ""), \
             patch.object(config, "SMTP_TO", ""):
            missing = _validate_smtp_config()
            self.assertEqual(len(missing), 3)


# ── send_report ─────────────────────────────────────────────

class TestSendReport(unittest.TestCase):

    _SMTP_PATCHES = {
        "SMTP_HOST": "smtp.test.com",
        "SMTP_PORT": 587,
        "SMTP_USER": "user@test.com",
        "SMTP_PASSWORD": "secret",
        "SMTP_FROM": "from@test.com",
        "SMTP_TO": "to@test.com",
    }

    def _patch_smtp_config(self):
        """Apply all SMTP config patches and return a list of patchers."""
        patchers = []
        for attr, val in self._SMTP_PATCHES.items():
            p = patch.object(config, attr, val)
            p.start()
            patchers.append(p)
        return patchers

    def test_happy_path(self):
        patchers = self._patch_smtp_config()
        try:
            mock_smtp = MagicMock()
            with patch("src.reporter.smtplib.SMTP",
                        return_value=mock_smtp):
                mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
                mock_smtp.__exit__ = MagicMock(return_value=False)

                result = send_report("Test Subject", "Test body")

            self.assertTrue(result["success"])
            self.assertEqual(result["error"], "")
            mock_smtp.starttls.assert_called_once()
            mock_smtp.login.assert_called_once_with("user@test.com", "secret")
            mock_smtp.send_message.assert_called_once()
        finally:
            for p in patchers:
                p.stop()

    def test_no_auth_when_creds_missing(self):
        patchers = self._patch_smtp_config()
        # Clear user/password
        p1 = patch.object(config, "SMTP_USER", "")
        p2 = patch.object(config, "SMTP_PASSWORD", "")
        p1.start()
        p2.start()
        patchers.extend([p1, p2])
        try:
            mock_smtp = MagicMock()
            with patch("src.reporter.smtplib.SMTP",
                        return_value=mock_smtp):
                mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
                mock_smtp.__exit__ = MagicMock(return_value=False)

                result = send_report("Subj", "Body")

            self.assertTrue(result["success"])
            mock_smtp.login.assert_not_called()
        finally:
            for p in patchers:
                p.stop()

    def test_missing_config_returns_error(self):
        with patch.object(config, "SMTP_HOST", ""), \
             patch.object(config, "SMTP_FROM", "a@b.com"), \
             patch.object(config, "SMTP_TO", "c@d.com"):
            result = send_report("Subj", "Body")
            self.assertFalse(result["success"])
            self.assertIn("SMTP_HOST", result["error"])

    def test_smtp_error_caught(self):
        import smtplib as _smtplib

        patchers = self._patch_smtp_config()
        try:
            mock_smtp = MagicMock()
            mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
            mock_smtp.__exit__ = MagicMock(return_value=False)
            mock_smtp.send_message.side_effect = _smtplib.SMTPException(
                "relay denied")

            with patch("src.reporter.smtplib.SMTP",
                        return_value=mock_smtp):
                result = send_report("Subj", "Body")

            self.assertFalse(result["success"])
            self.assertIn("SMTP error", result["error"])
        finally:
            for p in patchers:
                p.stop()

    def test_connection_error_caught(self):
        patchers = self._patch_smtp_config()
        try:
            with patch("src.reporter.smtplib.SMTP",
                        side_effect=OSError("Connection refused")):
                result = send_report("Subj", "Body")

            self.assertFalse(result["success"])
            self.assertIn("Connection error", result["error"])
        finally:
            for p in patchers:
                p.stop()


# ── generate_and_send_report ────────────────────────────────

class TestGenerateAndSendReport(_TempDBTestCase):

    def test_end_to_end(self):
        store_invoice("e2e.png", "hash_e2e", None, self._make_parsed())
        self._insert_processing_log("e2e.png", status="success")

        with patch.object(config, "SMTP_HOST", "smtp.test.com"), \
             patch.object(config, "SMTP_PORT", 587), \
             patch.object(config, "SMTP_USER", "u"), \
             patch.object(config, "SMTP_PASSWORD", "p"), \
             patch.object(config, "SMTP_FROM", "f@t.com"), \
             patch.object(config, "SMTP_TO", "t@t.com"):

            mock_smtp = MagicMock()
            mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
            mock_smtp.__exit__ = MagicMock(return_value=False)

            with patch("src.reporter.smtplib.SMTP",
                        return_value=mock_smtp):
                result = generate_and_send_report()

        self.assertTrue(result["success"])
        self.assertIn("InvoiceOCR Summary", result["subject"])
        self.assertIn("e2e.png", result["body"])
        self.assertEqual(result["data"]["total"], 1)

    def test_end_to_end_no_smtp(self):
        """Without SMTP config, generate_and_send_report returns error."""
        store_invoice("no_smtp.png", "hash_ns", None, self._make_parsed())

        with patch.object(config, "SMTP_HOST", ""), \
             patch.object(config, "SMTP_FROM", ""), \
             patch.object(config, "SMTP_TO", ""):
            result = generate_and_send_report()

        self.assertFalse(result["success"])
        self.assertIn("SMTP not configured", result["error"])
        # Body and subject should still be populated
        self.assertIn("InvoiceOCR", result["body"])
        self.assertIn("InvoiceOCR Summary", result["subject"])

    def test_empty_db_still_sends(self):
        with patch.object(config, "SMTP_HOST", "smtp.test.com"), \
             patch.object(config, "SMTP_PORT", 587), \
             patch.object(config, "SMTP_USER", "u"), \
             patch.object(config, "SMTP_PASSWORD", "p"), \
             patch.object(config, "SMTP_FROM", "f@t.com"), \
             patch.object(config, "SMTP_TO", "t@t.com"):

            mock_smtp = MagicMock()
            mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
            mock_smtp.__exit__ = MagicMock(return_value=False)

            with patch("src.reporter.smtplib.SMTP",
                        return_value=mock_smtp):
                result = generate_and_send_report()

        self.assertTrue(result["success"])
        self.assertIn("0 invoices", result["subject"])
        self.assertIn("No invoices processed", result["body"])


if __name__ == "__main__":
    unittest.main()
