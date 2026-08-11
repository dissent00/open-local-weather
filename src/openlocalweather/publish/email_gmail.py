"""Gmail SMTP direct-send EmailSender.

Sends via Gmail's own SMTP servers using an app password, rather than a
third-party ESP like Brevo — this sidesteps the DKIM-alignment problem
entirely (Google signs its own mail, so there's no "third party sending as
gmail.com" issue), but it's a deliberate, informed trade-off with a real
downside: GitHub Actions runner IPs are shared, rotating, cloud
infrastructure well-known to Google's abuse detection as automation
traffic, and there's a genuine chance the sending account gets flagged or
temporarily locked for "suspicious activity." This was chosen anyway to
avoid the custom-domain/DKIM setup Brevo (or any third-party ESP) requires
for real subscriber delivery under Google/Yahoo/Microsoft's 2024
bulk-sender rules. If lockouts become a recurring problem, migrate to
publish/email_brevo.py (a verified-domain ESP) instead — swapping is just a
different EmailSender implementation behind the same Protocol in
pipeline.py, no pipeline changes needed.

Free Gmail accounts cap out around 500 recipients/day. This sender doesn't
attempt to chunk or rate-limit beyond what smtplib does naturally; a
subscriber list approaching that limit needs a different provider
regardless of the DKIM question.
"""

from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import markdown

from openlocalweather.models import DailyLogEntry

GMAIL_SMTP_HOST = "smtp.gmail.com"
GMAIL_SMTP_PORT = 587
SMTP_TIMEOUT_S = 30


class GmailSMTPSender:
    """EmailSender implementation (pipeline.py's Protocol)."""

    def __init__(
        self,
        gmail_address: str,
        gmail_app_password: str,
        recipients: list[str],
        location_name: str,
    ):
        if not gmail_address or not gmail_app_password:
            raise ValueError("GmailSMTPSender requires gmail_address and gmail_app_password.")
        self.gmail_address = gmail_address
        self.gmail_app_password = gmail_app_password
        self.recipients = recipients
        self.location_name = location_name

    def send(self, entry: DailyLogEntry) -> None:
        if not self.recipients:
            return

        subject = f"[{self.location_name} Weather] Daily Forecast — {entry.date.isoformat()}"
        html_body = render_email_html(entry, self.location_name)

        with smtplib.SMTP(GMAIL_SMTP_HOST, GMAIL_SMTP_PORT, timeout=SMTP_TIMEOUT_S) as smtp:
            smtp.starttls()
            smtp.login(self.gmail_address, self.gmail_app_password)
            for recipient in self.recipients:
                msg = MIMEMultipart("alternative")
                msg["Subject"] = subject
                msg["From"] = self.gmail_address
                msg["To"] = recipient
                msg.attach(MIMEText(html_body, "html"))
                try:
                    smtp.sendmail(self.gmail_address, [recipient], msg.as_string())
                except smtplib.SMTPException as e:
                    # Best-effort per-recipient — one bad address shouldn't
                    # abort the whole run, mirroring the original pipeline's
                    # per-address try/catch in sendEmailBroadcast().
                    print(f"Failed to send to {recipient}: {e}")


def render_email_html(entry: DailyLogEntry, location_name: str) -> str:
    narrative_html = markdown.markdown(entry.narrative_markdown, extensions=["extra"])
    return f"""
<div style="font-family: -apple-system, Arial, sans-serif; line-height: 1.6; color: #222; max-width: 650px; margin: 0 auto;">
  <h2 style="color: #1a6fd1; margin-bottom: 4px;">{location_name} Daily Forecast</h2>
  <p style="font-size: 0.9em; color: #666; margin-top: 0;">Date: {entry.date.isoformat()}</p>
  <hr style="border: 0; border-top: 1px solid #ddd;">
  <div>{narrative_html}</div>
  <hr style="border: 0; border-top: 1px solid #ddd; margin-top: 20px;">
  <p style="font-size: 0.8em; color: #888;">You are receiving this because you subscribed to this forecast service.</p>
</div>
"""


def parse_recipient_list(raw: str) -> list[str]:
    """Parses a comma-separated SUBSCRIBER_EMAILS env var into a clean
    list, dropping blanks from stray commas/whitespace."""
    return [email.strip() for email in raw.split(",") if email.strip()]
