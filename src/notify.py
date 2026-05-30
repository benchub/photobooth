"""Out-of-band alerts to a phone, over whatever channels are configured.

Two independent channels (see AlertsConfig):

  - Email-to-SMS gateway: US carriers deliver email sent to a per-number gateway
    address as an SMS (T-Mobile's is `<10-digit-number>@tmomail.net`). Free but
    flaky.
  - ntfy push (https://ntfy.sh): an HTTP POST to a topic URL; subscribe to the
    topic in the ntfy app. More reliable, no account needed.

`send_alert()` fans out to every configured channel. Each send runs on its own
daemon thread so a slow/unreachable server never stalls the booth's GUI event
loop, and a failure on one channel never crashes the booth or blocks the other.
"""

from __future__ import annotations

import logging
import smtplib
import threading
from collections.abc import Callable
from email.message import EmailMessage

import requests

from .config import AlertsConfig

LOG = logging.getLogger(__name__)


def _send_email_blocking(cfg: AlertsConfig, subject: str, body: str) -> None:
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
        LOG.info("sent email/SMS alert to %s", cfg.sms_to)
    except Exception as e:
        # An alert that fails must never take down the booth — just log it.
        LOG.error("email/SMS alert to %s failed: %s", cfg.sms_to, e)


def _send_ntfy_blocking(cfg: AlertsConfig, subject: str, body: str) -> None:
    url = f"{cfg.ntfy_server.rstrip('/')}/{cfg.ntfy_topic}"
    headers = {"Title": subject}
    if cfg.ntfy_priority:
        headers["Priority"] = cfg.ntfy_priority
    if cfg.ntfy_token:
        headers["Authorization"] = f"Bearer {cfg.ntfy_token}"
    try:
        r = requests.post(url, data=body.encode("utf-8"), headers=headers, timeout=20)
        r.raise_for_status()
        LOG.info("sent ntfy alert to %s", url)
    except Exception as e:
        # An alert that fails must never take down the booth — just log it.
        LOG.error("ntfy alert to %s failed: %s", url, e)


def send_alert(cfg: AlertsConfig, body: str, subject: str = "Photobooth") -> bool:
    """Fire-and-forget alert over every configured channel (email gateway, ntfy).

    Returns False (and does nothing) if no channel is configured. Returns True
    once at least one send has been handed to a background thread — not a
    delivery guarantee; failures are logged per channel.
    """
    senders: list[tuple[str, Callable[[AlertsConfig, str, str], None]]] = []
    if cfg.sms_to and cfg.smtp_host:
        senders.append(("email", _send_email_blocking))
    if cfg.ntfy_topic:
        senders.append(("ntfy", _send_ntfy_blocking))
    if not senders:
        LOG.warning(
            "alert skipped: configure alerts.sms_to+smtp_host and/or alerts.ntfy_topic"
        )
        return False
    for name, fn in senders:
        threading.Thread(
            target=fn,
            args=(cfg, subject, body),
            name=f"alert-{name}",
            daemon=True,
        ).start()
    return True
