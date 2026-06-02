"""Outbound channels for reminders + login codes.

Console by default (prints to stdout so dev — and a no-provider Vercel deploy —
works without Resend/Twilio: the sign-in code shows up in the Vercel runtime
logs). Resend for email and Twilio for SMS in prod. Functions return True only
on a confirmed send, so the reminder log can be committed *after* success
(at-least-once idempotency — plan §7).
"""
from __future__ import annotations

import logging
import smtplib
import sys
from email.message import EmailMessage

import httpx

from .config import settings

log = logging.getLogger("mtd.notify")


def _console(channel: str, to: str, body: str, subject: str | None = None) -> None:
    """Print a clearly-delimited block to stdout (shows in Vercel runtime logs)."""
    head = f"[MTD {channel}] -> {to}" + (f"  |  {subject}" if subject else "")
    print(f"\n{'=' * 60}\n{head}\n{'-' * 60}\n{body}\n{'=' * 60}\n", flush=True)
    log.info("%s to %s", channel, to)


def send_email(to: str, subject: str, html: str, text: str | None = None) -> bool:
    provider = settings.effective_email
    if provider == "resend":
        try:
            r = httpx.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {settings.resend_api_key}"},
                json={"from": settings.email_from, "to": [to],
                      "subject": subject, "html": html,
                      "text": text or _strip(html)},
                timeout=15)
            r.raise_for_status()
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("Resend email to %s failed: %s", to, exc)
            return False
    if provider == "smtp":
        return _send_smtp(to, subject, html, text)
    # console fallback — the sign-in code lands here (and in Vercel logs)
    _console("EMAIL", to, text or _strip(html), subject)
    return True


def _send_smtp(to: str, subject: str, html: str, text: str | None = None) -> bool:
    msg = EmailMessage()
    msg["From"] = settings.mail_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text or _strip(html))
    msg.add_alternative(html, subtype="html")
    try:
        if settings.mail_port == 465:  # implicit TLS
            with smtplib.SMTP_SSL(settings.mail_server, settings.mail_port, timeout=20) as srv:
                srv.login(settings.email_user, settings.email_pass)
                srv.send_message(msg)
        else:  # STARTTLS (587)
            with smtplib.SMTP(settings.mail_server, settings.mail_port, timeout=20) as srv:
                srv.ehlo()
                srv.starttls()
                srv.login(settings.email_user, settings.email_pass)
                srv.send_message(msg)
        log.info("SMTP email sent to %s via %s", to, settings.mail_server)
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("SMTP email to %s via %s:%s failed: %s",
                  to, settings.mail_server, settings.mail_port, exc)
        return False


def send_sms(to: str, body: str) -> bool:
    provider = settings.effective_sms
    if provider == "twilio":
        try:
            r = httpx.post(
                f"https://api.twilio.com/2010-04-01/Accounts/"
                f"{settings.twilio_account_sid}/Messages.json",
                data={"From": settings.twilio_from, "To": to, "Body": body},
                auth=(settings.twilio_account_sid, settings.twilio_auth_token),
                timeout=15)
            r.raise_for_status()
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("Twilio SMS to %s failed: %s", to, exc)
            return False
    _console("SMS", to, body)
    return True


def _strip(html: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", html).strip()
