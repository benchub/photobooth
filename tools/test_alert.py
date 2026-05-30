"""Send a test alert through each configured channel, loudly.

Use this to debug phone alerts without launching the whole booth. It loads the
same config the app does (config.yaml + env / .env, including
PHOTOBOOTH_ALERTS_SMTP_PASSWORD and PHOTOBOOTH_ALERTS_NTFY_TOKEN), prints what it
resolved, and tries one synchronous send per configured channel — printing the
full error if it fails (the booth itself only logs these to
~/.photobooth/log.txt on a background thread).

Run: `python tools/test_alert.py`
"""

from __future__ import annotations

import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

import requests

# Make `src` importable when run as a loose script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config  # noqa: E402

BODY = "Photobooth alert test — if you got this, alerts work."


def check_email(cfg) -> int | None:
    """Returns None if the email channel isn't configured, else 0/1/2 exit code."""
    if not (cfg.sms_to or cfg.smtp_host):
        return None

    pw = cfg.smtp_password or ""
    print("Email-to-SMS channel:")
    print(f"  sms_to:        {cfg.sms_to!r}")
    print(f"  smtp_host:     {cfg.smtp_host!r}")
    print(f"  smtp_port:     {cfg.smtp_port}")
    print(f"  smtp_user:     {cfg.smtp_user!r}")
    print(f"  smtp_from:     {cfg.smtp_from!r} (defaults to smtp_user if empty)")
    print(f"  smtp_starttls: {cfg.smtp_starttls}")
    print(f"  password set:  {'yes' if pw else 'NO'}"
          + (f" (length {len(pw)})" if pw else ""))

    problems = []
    if not cfg.sms_to:
        problems.append("alerts.sms_to is empty (e.g. 5551234567@tmomail.net)")
    if not cfg.smtp_host:
        problems.append("alerts.smtp_host is empty (e.g. smtp.gmail.com)")
    if not pw:
        problems.append(
            "no password — set PHOTOBOOTH_ALERTS_SMTP_PASSWORD in your .env"
        )
    if pw and " " in pw:
        problems.append(
            "password contains spaces — Gmail shows app passwords as "
            "'abcd efgh ijkl mnop' but you must enter them with NO spaces (16 chars)"
        )
    if pw and cfg.smtp_host == "smtp.gmail.com" and len(pw.replace(" ", "")) != 16:
        problems.append(
            f"Gmail app passwords are 16 characters; yours is "
            f"{len(pw.replace(' ', ''))} — is it a regular password instead of an "
            "app password?"
        )
    if problems:
        print("\n  Configuration problems:")
        for p in problems:
            print(f"    - {p}")
        if not (cfg.sms_to and cfg.smtp_host and pw):
            return 2  # can't even attempt a send

    print(f"\n  Sending to {cfg.sms_to} via {cfg.smtp_host}:{cfg.smtp_port} …")
    msg = EmailMessage()
    msg["From"] = cfg.smtp_from or cfg.smtp_user
    msg["To"] = cfg.sms_to
    msg["Subject"] = "Photobooth"
    msg.set_content(BODY)
    try:
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=20) as s:
            s.set_debuglevel(1)  # print the SMTP conversation
            if cfg.smtp_starttls:
                s.starttls()
            if cfg.smtp_user:
                s.login(cfg.smtp_user, pw)
            s.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        print(f"\n  AUTH FAILED: {e}")
        print(
            "  Gmail rejected the login. Checklist:\n"
            "    1. 2-Step Verification must be ON for the account.\n"
            "    2. Use an App Password (https://myaccount.google.com/apppasswords),\n"
            "       NOT your normal password.\n"
            "    3. Enter the 16-char app password with NO spaces.\n"
            "    4. smtp_user must be the full address (you@gmail.com)."
        )
        return 1
    except Exception as e:
        print(f"\n  SEND FAILED: {type(e).__name__}: {e}")
        return 1

    print("\n  OK — sent. Check your phone (gateway delivery can take a minute).")
    return 0


def check_ntfy(cfg) -> int | None:
    """Returns None if the ntfy channel isn't configured, else 0/1 exit code."""
    if not cfg.ntfy_topic:
        return None

    url = f"{cfg.ntfy_server.rstrip('/')}/{cfg.ntfy_topic}"
    print("ntfy channel:")
    print(f"  ntfy_server:   {cfg.ntfy_server!r}")
    print(f"  ntfy_topic:    {cfg.ntfy_topic!r}")
    print(f"  ntfy_priority: {cfg.ntfy_priority!r} (optional)")
    print(f"  token set:     {'yes' if cfg.ntfy_token else 'no'}")
    print(f"\n  Posting to {url} …")

    headers = {"Title": "Photobooth"}
    if cfg.ntfy_priority:
        headers["Priority"] = cfg.ntfy_priority
    if cfg.ntfy_token:
        headers["Authorization"] = f"Bearer {cfg.ntfy_token}"
    try:
        r = requests.post(url, data=BODY.encode("utf-8"), headers=headers, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"\n  SEND FAILED: {type(e).__name__}: {e}")
        return 1

    print(f"\n  OK — posted (HTTP {r.status_code}). Check the ntfy app subscribed "
          f"to topic {cfg.ntfy_topic!r}.")
    return 0


def main() -> int:
    cfg = load_config().alerts

    results = []
    for name, fn in (("email/SMS", check_email), ("ntfy", check_ntfy)):
        rc = fn(cfg)
        if rc is None:
            print(f"{name} channel: not configured — skipping.\n")
        else:
            results.append(rc)
            print()

    if not results:
        print("No alert channels configured. Set up alerts.sms_to+smtp_host "
              "and/or alerts.ntfy_topic in config.yaml.")
        return 2
    # Non-zero if any configured channel failed.
    return max(results)


if __name__ == "__main__":
    sys.exit(main())
