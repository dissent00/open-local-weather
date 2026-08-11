from datetime import date, datetime, timezone

import pytest

from openlocalweather.models import DailyLogEntry, LogEntryMeta, ModelPredictionsByLead
from openlocalweather.publish import email_gmail
from openlocalweather.publish.email_gmail import GmailSMTPSender, parse_recipient_list, render_email_html


def make_entry(**overrides) -> DailyLogEntry:
    defaults = dict(
        date=date(2026, 8, 11),
        rain_expected="Likely",
        temp_high_c=26.0,
        temp_low_c=18.0,
        temp_high_low_display="26°C / 79°F",
        mslp_trend_24h="falling",
        synoptic_pattern="trough",
        narrative_markdown="## Overview\nRain **likely** today.",
        model_predictions=ModelPredictionsByLead(),
        meta=LogEntryMeta(
            generated_at_utc=datetime.now(timezone.utc), llm_provider="gemini", llm_model="test", pipeline_version="0"
        ),
    )
    defaults.update(overrides)
    return DailyLogEntry(**defaults)


# ---------------------------------------------------------------------------
# parse_recipient_list
# ---------------------------------------------------------------------------


def test_parse_recipient_list_splits_and_strips():
    assert parse_recipient_list("a@example.com, b@example.com ,c@example.com") == [
        "a@example.com", "b@example.com", "c@example.com",
    ]


def test_parse_recipient_list_drops_blanks():
    assert parse_recipient_list("a@example.com,,  ,b@example.com") == ["a@example.com", "b@example.com"]


def test_parse_recipient_list_empty_string():
    assert parse_recipient_list("") == []


# ---------------------------------------------------------------------------
# render_email_html
# ---------------------------------------------------------------------------


def test_render_email_html_includes_location_and_narrative():
    entry = make_entry()
    html = render_email_html(entry, "Kisumu, Kenya")
    assert "Kisumu, Kenya Daily Forecast" in html
    assert "2026-08-11" in html
    assert "<strong>likely</strong>" in html


# ---------------------------------------------------------------------------
# GmailSMTPSender
# ---------------------------------------------------------------------------


def test_requires_address_and_password():
    with pytest.raises(ValueError):
        GmailSMTPSender("", "pw", ["a@example.com"], "Kisumu")
    with pytest.raises(ValueError):
        GmailSMTPSender("me@gmail.com", "", ["a@example.com"], "Kisumu")


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.starttls_called = False
        self.login_calls = []
        self.sendmail_calls = []
        self.raise_on_recipient: str | None = None
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.starttls_called = True

    def login(self, address, password):
        self.login_calls.append((address, password))

    def sendmail(self, from_addr, to_addrs, msg):
        if self.raise_on_recipient and self.raise_on_recipient in to_addrs:
            import smtplib
            raise smtplib.SMTPException("boom")
        self.sendmail_calls.append((from_addr, to_addrs, msg))


def test_send_no_recipients_does_not_connect(monkeypatch):
    monkeypatch.setattr(email_gmail.smtplib, "SMTP", FakeSMTP)
    FakeSMTP.instances.clear()
    sender = GmailSMTPSender("me@gmail.com", "app-password", [], "Kisumu")
    sender.send(make_entry())
    assert FakeSMTP.instances == []


def test_send_connects_and_sends_to_each_recipient(monkeypatch):
    monkeypatch.setattr(email_gmail.smtplib, "SMTP", FakeSMTP)
    FakeSMTP.instances.clear()
    sender = GmailSMTPSender(
        "me@gmail.com", "app-password", ["a@example.com", "b@example.com"], "Kisumu"
    )
    sender.send(make_entry())

    smtp = FakeSMTP.instances[0]
    assert smtp.host == email_gmail.GMAIL_SMTP_HOST
    assert smtp.port == email_gmail.GMAIL_SMTP_PORT
    assert smtp.starttls_called is True
    assert smtp.login_calls == [("me@gmail.com", "app-password")]
    assert len(smtp.sendmail_calls) == 2
    sent_to = [call[1] for call in smtp.sendmail_calls]
    assert ["a@example.com"] in sent_to
    assert ["b@example.com"] in sent_to


def test_send_one_bad_recipient_does_not_block_others(monkeypatch):
    monkeypatch.setattr(email_gmail.smtplib, "SMTP", FakeSMTP)
    FakeSMTP.instances.clear()

    def factory(host, port, timeout=None):
        smtp = FakeSMTP(host, port, timeout)
        smtp.raise_on_recipient = "bad@example.com"
        return smtp

    monkeypatch.setattr(email_gmail.smtplib, "SMTP", factory)
    sender = GmailSMTPSender(
        "me@gmail.com", "app-password", ["bad@example.com", "good@example.com"], "Kisumu"
    )
    sender.send(make_entry())  # must not raise

    smtp = FakeSMTP.instances[0]
    assert len(smtp.sendmail_calls) == 1
    assert smtp.sendmail_calls[0][1] == ["good@example.com"]


def test_send_subject_and_from_are_correct(monkeypatch):
    monkeypatch.setattr(email_gmail.smtplib, "SMTP", FakeSMTP)
    FakeSMTP.instances.clear()
    sender = GmailSMTPSender("me@gmail.com", "app-password", ["a@example.com"], "Kisumu, Kenya")
    sender.send(make_entry())

    smtp = FakeSMTP.instances[0]
    _, _, raw_msg = smtp.sendmail_calls[0]

    import email as email_lib
    parsed = email_lib.message_from_string(raw_msg)
    # Subject contains a non-ASCII em dash, so it's RFC 2047 encoded on the
    # wire — decode it back before asserting on the literal text.
    subject_header, encoding = email_lib.header.decode_header(parsed["Subject"])[0]
    if isinstance(subject_header, bytes):
        subject_header = subject_header.decode(encoding or "ascii")
    assert subject_header == "[Kisumu, Kenya Weather] Daily Forecast — 2026-08-11"
    assert parsed["From"] == "me@gmail.com"
