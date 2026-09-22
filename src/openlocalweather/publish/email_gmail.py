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


from openlocalweather.models import DailyLogEntry
from html import escape

from openlocalweather.publish.narrative import narrative_to_html
from openlocalweather.tiles import compose_tiles

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


# THE TILES, IN TABLE MARKUP. An email client is not a browser: Gmail strips
# <style> blocks, Outlook's desktop clients render through Word, and CSS grid
# and flexbox are unreliable in both. A two-column table with inline styles is
# what actually arrives, which is why this does not share the page's markup
# even though it shares the page's CONTENT.
#
# WHY IT EXISTS AT ALL — ROADMAP item 159 step 6. Until now the email body was
# the narrative and nothing else. That was survivable while the narrative
# opened with an Overview summarising the day; step 5 retired the Overview, so
# an email reader was left with no at-a-glance anything. This is the half of
# that change the email was owed.
EMAIL_TILE_COLUMNS = 2


def _tile_cell(tile: dict) -> str:
    heading = tile["label"] + (f" ({tile['unit']})" if tile["unit"] else "")
    lines = "".join(
        f'<div style="font-size:{"1.05em;font-weight:600" if line["primary"] else "0.85em;color:#666"};'
        f'line-height:1.35;">{escape(line["text"])}</div>'
        for line in tile["lines"]
    )

    return (
        '<td width="50%" valign="top" style="padding:0 10px 14px 0;">'
        f'<div style="font-size:0.72em;color:#888;text-transform:uppercase;'
        f'letter-spacing:0.04em;padding-bottom:2px;">{escape(heading)}</div>'
        f"{lines}</td>"
    )


def render_tile_table(entry: DailyLogEntry) -> str:
    """The at-a-glance tiles as an email-safe table, or nothing.

    Empty when the record fills no tile, which renders no table rather than an
    empty one — the same rule the tiles themselves follow.
    """
    tiles = compose_tiles(entry.model_dump(mode="json"), metric=True)
    if not tiles:
        return ""

    rows = "".join(
        "<tr>"
        + "".join(_tile_cell(t) for t in tiles[i : i + EMAIL_TILE_COLUMNS])
        + "</tr>"
        for i in range(0, len(tiles), EMAIL_TILE_COLUMNS)
    )

    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        f'width="100%" style="margin:12px 0 4px;">{rows}</table>'
    )


def render_email_html(entry: DailyLogEntry, location_name: str) -> str:
    # Same converter as the page, and for the same reason — see
    # publish/narrative.py. This is interpolated into an f-string with no
    # escaping of any kind, so it is the more exposed of the two call sites.
    narrative_html = narrative_to_html(entry.narrative_markdown)
    return f"""
<div style="font-family: -apple-system, Arial, sans-serif; line-height: 1.6; color: #222; max-width: 650px; margin: 0 auto;">
  <h2 style="color: #1a6fd1; margin-bottom: 4px;">{location_name} Daily Forecast</h2>
  <p style="font-size: 0.9em; color: #666; margin-top: 0;">Date: {entry.date.isoformat()}</p>
  <hr style="border: 0; border-top: 1px solid #ddd;">
  {render_tile_table(entry)}
  <div>{narrative_html}</div>
  <hr style="border: 0; border-top: 1px solid #ddd; margin-top: 20px;">
  <p style="font-size: 0.8em; color: #888;">You are receiving this because you subscribed to this forecast service.</p>
</div>
"""


def parse_recipient_list(raw: str) -> list[str]:
    """Parses a comma-separated SUBSCRIBER_EMAILS env var into a clean
    list, dropping blanks from stray commas/whitespace."""
    return [email.strip() for email in raw.split(",") if email.strip()]
