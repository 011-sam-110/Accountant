"""Outbound channels for reminders + login codes.

Console by default (prints to the server log so dev works with no provider);
Resend for email and Twilio for SMS in prod. Functions return True only on a
confirmed send, so the reminder log can be committed *after* success (at-least-
once idempotency — plan §7).
"""
from __future__ import annotations

import logging

import httpx

from .config import settings

log = logging.getLogger("mtd.notify")


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
    # console fallback
    log.info("\n=== EMAIL (console) ===\nTo: %s\nSubject: %s\n\n%s\n=======================",
             to, subject, text or _strip(html))
    return True


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
    log.info("\n=== SMS (console) ===\nTo: %s\n\n%s\n=====================", to, body)
    return True


def _strip(html: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", html).strip()
