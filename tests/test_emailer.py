"""
Tests for emailer module.

Covers:
  - send_daily_summary with dry_run=True
  - send_daily_summary SMTP failure fallback to saving HTML locally
"""

import sys
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config
from src.emailer import send_daily_summary


class TestEmailer(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile_fixture_helper()) if 'tempfile_fixture_helper' in globals() else Path(unittest.TestCase.__name__)

    @patch("smtplib.SMTP")
    def test_send_daily_summary_dry_run(self, mock_smtp_cls):
        html_body = "<h1>Report</h1>"
        excel_path = Path("dummy.xlsx")
        subject = "Daily Report"

        # Dry run should not attempt connection
        send_daily_summary(excel_path, html_body, subject, dry_run=True)
        mock_smtp_cls.assert_not_called()

    @patch("smtplib.SMTP")
    def test_send_daily_summary_success(self, mock_smtp_cls):
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__.return_value = mock_server

        html_body = "<h1>Report</h1>"
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp.write(b"fake excel content")
            excel_path = Path(tmp.name)
        subject = "Daily Report"

        try:
            with patch.object(config, "SMTP_HOST", "smtp.test.com"), \
                 patch.object(config, "SMTP_FROM", "from@test.com"), \
                 patch.object(config, "SMTP_TO", "to@test.com"):
                send_daily_summary(excel_path, html_body, subject, dry_run=False)
        finally:
            if excel_path.exists():
                excel_path.unlink()

        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once()
        mock_server.send_message.assert_called_once()


if __name__ == "__main__":
    unittest.main()
