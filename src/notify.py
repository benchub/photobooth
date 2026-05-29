"""Out-of-band alerts: text a phone via a carrier email-to-SMS gateway.

US carriers deliver email sent to a per-number gateway address as an SMS.
T-Mobile's gateway is `<10-digit-number>@tmomail.net`. Configure the recipient
and an SMTP relay under `alerts:` in config (see AlertsConfig). Sending happens
on a daemon thread so a slow/unreachable SMTP server never stalls the booth's
GUI event loop.
"""

from __future__ import annotations

import logging
import smtplib
import threading
from email.message import EmailMessage

from .config import AlertsConfig

LOG = logging.getLogger(__name__)


def _send_blocking(cfg: AlertsConfig, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = cfg.smtp_from or cfg.smtp_user
    msg["To"] = cfg.sms_to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=20) as s:
            if cfg.smtp_starttls:
                s.starttls()
            if cfg.smtp_user:
                s.login(cfg.smtp_user, cfg.smtp_password)
            s.send_message(msg)
        LOG.info("sent SMS alert to %s", cfg.sms_to)
    except Exception as e:
        # An alert that fails must never take down the booth — just log it.
        LOG.error("SMS alert to %s failed: %s", cfg.sms_to, e)


def send_sms_alert(cfg: AlertsConfig, body: str, subject: str = "Photobooth") -> bool:
    """Fire-and-forget SMS via the configured email gateway.

    Returns False (and does nothing) if alerts aren't configured. Returns True
    once the send has been handed to a background thread — not a delivery
    guarantee; failures are logged.
    """
    if not (cfg.sms_to and cfg.smtp_host):
        LOG.warning("SMS alert skipped: alerts.sms_to / alerts.smtp_host not set")
        return False
    threading.Thread(
        target=_send_blocking,
        args=(cfg, subject, body),
        name="sms-alert",
        daemon=True,
    ).start()
    return True
