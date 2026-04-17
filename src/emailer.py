"""Email rendering and delivery primitives.

This module handles template rendering, recipient validation, MIME construction,
and batch sending with progress callbacks.
"""

from __future__ import annotations

import smtplib
from dataclasses import dataclass
from html import escape as html_escape
from html import unescape as html_unescape
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from string import Formatter
from typing import Callable, Iterable
import re

import pandas as pd

from .config import SMTPSettings
from .templates import DEFAULT_EMAIL_BLACKLIST


@dataclass(frozen=True)
class BatchResult:
    sent: int
    failed: int
    errors: list[str]


ProgressCallback = Callable[[float], None]
StatusCallback = Callable[[str], None]

EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
EMAIL_TRASH_PATTERNS = tuple(DEFAULT_EMAIL_BLACKLIST)


@dataclass(frozen=True)
class EmailAttachment:
    filename: str
    content: bytes
    mime_type: str = "application/octet-stream"


def extract_template_fields(template: str) -> set[str]:
    fields: set[str] = set()
    for _, field_name, _, _ in Formatter().parse(template):
        if field_name:
            fields.add(field_name)
    return fields


def validate_template_columns(template: str, columns: Iterable[str]) -> list[str]:
    column_set = set(columns)
    return sorted(field for field in extract_template_fields(template) if field not in column_set)


def render_template(template: str, values: dict[str, object]) -> str:
    return template.format(**values)


def render_html_template(template: str, values: dict[str, object]) -> str:
    safe_values = {key: html_escape(str(value)) for key, value in values.items()}
    return template.format(**safe_values)


def html_to_plain_text(html_body: str) -> str:
    text = re.sub(r"(?i)<br\\s*/?>", "\n", html_body)
    text = re.sub(r"(?i)</p>", "\n\n", text)
    text = re.sub(r"(?i)<li>\\s*", "- ", text)
    text = re.sub(r"(?i)</li>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def is_valid_email_address(email: str) -> bool:
    normalized = email.strip().lower()
    if not normalized or not EMAIL_PATTERN.fullmatch(normalized):
        return False
    return not any(pattern in normalized for pattern in EMAIL_TRASH_PATTERNS)


def build_email_message(
    sender_email: str,
    recipient_email: str,
    subject: str,
    plain_body: str,
    html_body: str,
    attachments: list[EmailAttachment] | None = None,
) -> MIMEMultipart:
    message = MIMEMultipart("mixed")
    message["From"] = sender_email
    message["To"] = recipient_email
    message["Subject"] = subject

    reserve_plain = plain_body.strip() or html_to_plain_text(html_body)
    alternative = MIMEMultipart("alternative")
    alternative.attach(MIMEText(reserve_plain, "plain", "utf-8"))
    alternative.attach(MIMEText(html_body, "html", "utf-8"))
    message.attach(alternative)

    for attachment in attachments or []:
        main_type, _, sub_type = attachment.mime_type.partition("/")
        if not sub_type:
            main_type = "application"
            sub_type = "octet-stream"

        mime_part = MIMEBase(main_type, sub_type)
        mime_part.set_payload(attachment.content)
        encoders.encode_base64(mime_part)
        mime_part.add_header("Content-Disposition", f'attachment; filename="{attachment.filename}"')
        try:
            message.attach(mime_part)
        except Exception as exc:
            try:
                import streamlit as st

                st.error(f"Failed to attach file {attachment.filename}: {exc}")
            except Exception:
                pass
            raise

    return message


def send_email_ssl(
    settings: SMTPSettings,
    recipient_email: str,
    subject: str,
    plain_body: str,
    html_body: str,
    attachments: list[EmailAttachment] | None = None,
    dry_run: bool = False,
) -> None:
    if dry_run:
        print("[DRY RUN] Prepared email")
        print(f"To: {recipient_email}")
        print(f"From: {settings.sender_email}")
        print(f"Subject: {subject}")
        print("Body (plain):")
        print(plain_body)
        print("Body (html):")
        print(html_body)
        if attachments:
            print("Attachments:")
            for attachment in attachments:
                print(f"- {attachment.filename} ({len(attachment.content)} bytes)")
        print("-" * 60)
        return

    message = build_email_message(
        sender_email=settings.sender_email,
        recipient_email=recipient_email,
        subject=subject,
        plain_body=plain_body,
        html_body=html_body,
        attachments=attachments,
    )

    with smtplib.SMTP_SSL(settings.server, settings.port, timeout=30) as server:
        server.login(settings.sender_email, settings.sender_password)
        server.send_message(message)


def process_email_batch(
    df: pd.DataFrame,
    settings: SMTPSettings,
    plain_template: str,
    html_template: str,
    recipient_column: str = "Email",
    attachments: list[EmailAttachment] | None = None,
    dry_run: bool = False,
    on_progress: ProgressCallback | None = None,
    on_status: StatusCallback | None = None,
) -> BatchResult:
    subject_missing_columns = validate_template_columns(settings.subject_template, df.columns)
    body_missing_columns = validate_template_columns(plain_template, df.columns)
    html_missing_columns = validate_template_columns(html_template, df.columns)
    missing_columns = sorted(set(subject_missing_columns + body_missing_columns + html_missing_columns))
    if missing_columns:
        raise KeyError(
            f"Template uses missing columns: {', '.join(missing_columns)}"
        )

    if recipient_column not in df.columns:
        raise KeyError(f"Missing recipient column: {recipient_column}")

    total_rows = len(df)
    sent_count = 0
    failed_count = 0
    errors: list[str] = []

    if total_rows == 0:
        return BatchResult(sent=0, failed=0, errors=[])

    for row_index, (_, row) in enumerate(df.iterrows(), start=1):
        try:
            row_data = {str(key): value for key, value in row.to_dict().items()}
            recipient_email = str(row_data.get(recipient_column, "")).strip()
            if not recipient_email:
                raise ValueError(
                    f"Row {row_index} has no recipient in column {recipient_column}."
                )
            if not is_valid_email_address(recipient_email):
                raise ValueError(f"Row {row_index} has invalid recipient email: {recipient_email}")

            subject = render_template(settings.subject_template, row_data)
            plain_body = render_template(plain_template, row_data)
            html_body = render_html_template(html_template, row_data)
            send_email_ssl(
                settings=settings,
                recipient_email=recipient_email,
                subject=subject,
                plain_body=plain_body,
                html_body=html_body,
                attachments=attachments,
                dry_run=dry_run,
            )
            sent_count += 1

            if on_status is not None:
                on_status(f"Processed: {recipient_email}")
        except Exception as exc:
            failed_count += 1
            error_message = f"Row {row_index}: {exc}"
            errors.append(error_message)
            try:
                import streamlit as st

                st.exception(exc)
            except Exception:
                pass
            if on_status is not None:
                on_status(error_message)
        finally:
            if on_progress is not None:
                on_progress(row_index / total_rows)

    return BatchResult(sent=sent_count, failed=failed_count, errors=errors)
