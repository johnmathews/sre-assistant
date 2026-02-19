"""Email delivery for weekly reliability reports.

Uses stdlib smtplib with STARTTLS for Gmail SMTP.  All functions are
designed to never raise — they return success/failure booleans and log errors.
"""

import logging
import smtplib
from email.mime.text import MIMEText

from src.config import get_settings

logger = logging.getLogger(__name__)


def is_email_configured() -> bool:
    """Check whether all required SMTP settings are present."""
    settings = get_settings()
    return bool(
        settings.smtp_host and settings.smtp_username and settings.smtp_password and settings.report_recipient_email
    )


def send_report_email(markdown_report: str, subject: str | None = None) -> bool:
    """Send a plain-text markdown report via SMTP with STARTTLS.

    Args:
        markdown_report: The report body as markdown text.
        subject: Optional email subject. Defaults to a timestamped subject.

    Returns:
        True if the email was sent successfully, False otherwise.
    """
    settings = get_settings()

    if not is_email_configured():
        logger.warning("Email not configured — skipping send")
        return False

    if subject is None:
        subject = "SRE Assistant — Weekly Reliability Report"

    msg = MIMEText(markdown_report, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = settings.smtp_username
    msg["To"] = settings.report_recipient_email

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as server:
            _ = server.starttls()
            server.login(settings.smtp_username, settings.smtp_password)
            server.send_message(msg)
        logger.info("Report email sent to %s", settings.report_recipient_email)
        return True
    except Exception:
        logger.exception("Failed to send report email")
        return False
