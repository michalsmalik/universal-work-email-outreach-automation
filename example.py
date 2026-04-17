"""CLI example entrypoint for generic outreach sending.

This script demonstrates safe and reusable usage without personal defaults.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from src.config import load_smtp_settings
from src.emailer import EmailAttachment, process_email_batch, validate_template_columns
from src.templates import (
    DEFAULT_OUTREACH_HTML_TEMPLATE,
    DEFAULT_OUTREACH_SUBJECT_TEMPLATE,
    DEFAULT_SENDER_NAME,
    DEFAULT_SENDER_PHONE,
    DEFAULT_LINKEDIN_LABEL,
    OutreachIdentity,
    default_outreach_plain_template,
)


def load_dataframe(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".xlsx":
        return pd.read_excel(path, engine="openpyxl")
    if suffix == ".csv":
        return pd.read_csv(path)
    raise ValueError("Input file must be .xlsx or .csv")


def read_attachments(files: list[str]) -> list[EmailAttachment]:
    attachments: list[EmailAttachment] = []
    for raw in files:
        path = Path(raw)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"Attachment not found: {path}")
        attachments.append(
            EmailAttachment(
                filename=path.name,
                content=path.read_bytes(),
                mime_type="application/octet-stream",
            )
        )
    return attachments


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Simple example sender using generic templates (no personal data hardcoded)."
    )
    parser.add_argument("--input", required=True, help="Path to .xlsx or .csv file")
    parser.add_argument(
        "--recipient-column",
        default="email",
        help="Recipient email column name in input file",
    )
    parser.add_argument(
        "--attachment",
        action="append",
        default=[],
        help="Attachment path (repeat flag for multiple files)",
    )
    parser.add_argument("--send", action="store_true", help="Send real emails (default is dry-run)")
    parser.add_argument("--subject", default=DEFAULT_OUTREACH_SUBJECT_TEMPLATE)
    parser.add_argument("--html-template", default=DEFAULT_OUTREACH_HTML_TEMPLATE)
    parser.add_argument("--sender-name", default=DEFAULT_SENDER_NAME)
    parser.add_argument("--sender-phone", default=DEFAULT_SENDER_PHONE)
    parser.add_argument("--linkedin-label", default=DEFAULT_LINKEDIN_LABEL)
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    identity = OutreachIdentity(
        sender_name=args.sender_name,
        sender_phone=args.sender_phone,
        linkedin_label=args.linkedin_label,
    )
    plain_template = default_outreach_plain_template(identity)

    df = load_dataframe(input_path)
    if df.empty:
        raise ValueError("Input file has no rows")

    subject_missing = validate_template_columns(args.subject, df.columns)
    plain_missing = validate_template_columns(plain_template, df.columns)
    html_missing = validate_template_columns(args.html_template, df.columns)
    required_fields = ["sender_name", "sender_phone", "linkedin_label"]
    if any(field in plain_missing for field in required_fields):
        plain_missing = [field for field in plain_missing if field not in required_fields]
    missing = sorted(set(subject_missing + plain_missing + html_missing))
    if missing:
        raise KeyError(f"Input file is missing columns required by templates: {', '.join(missing)}")

    settings = load_smtp_settings(subject_template=args.subject, require_password=args.send)
    attachments = read_attachments(args.attachment)

    result = process_email_batch(
        df=df,
        settings=settings,
        plain_template=plain_template,
        html_template=args.html_template,
        recipient_column=args.recipient_column,
        attachments=attachments,
        dry_run=not args.send,
    )

    print(f"Done. Sent: {result.sent}, failed: {result.failed}")
    if result.errors:
        print("Errors:")
        for error in result.errors:
            print(f"- {error}")


if __name__ == "__main__":
    main()
