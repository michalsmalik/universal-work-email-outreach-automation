"""SMTP configuration loading utilities.

This module resolves SMTP credentials from environment variables and exposes
typed settings used by sending workflows.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class SMTPSettings:
    server: str
    port: int
    sender_email: str
    sender_password: str
    subject_template: str


def load_smtp_settings(subject_template: str, require_password: bool = True) -> SMTPSettings:
    load_dotenv()

    sender_email = os.getenv("EMAIL_USER", "").strip()
    sender_password = os.getenv("EMAIL_PASSWORD", "").strip()
    server = os.getenv("SMTP_SERVER", "smtp.gmail.com").strip() or "smtp.gmail.com"
    port_value = os.getenv("SMTP_PORT", "465").strip() or "465"

    if not sender_email:
        raise ValueError("Missing EMAIL_USER in .env file.")
    if require_password and not sender_password:
        raise ValueError("Missing EMAIL_PASSWORD in .env file.")

    try:
        port = int(port_value)
    except ValueError as exc:
        raise ValueError("SMTP_PORT must be an integer.") from exc

    return SMTPSettings(
        server=server,
        port=port,
        sender_email=sender_email,
        sender_password=sender_password,
        subject_template=subject_template,
    )
