"""Legacy Streamlit UI flow.

This module contains the older upload-and-send interface preserved for
compatibility and incremental migration.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

from .config import SMTPSettings, load_smtp_settings
from .data_loader import load_uploaded_file
from .emailer import EmailAttachment, process_email_batch, render_html_template, render_template
from .templates import MANDATORY_CV_FILES, DEFAULT_SUBJECT_TEMPLATE, DEFAULT_PLAIN_TEMPLATE, DEFAULT_HTML_TEMPLATE, ALLOWED_CV_EXTENSIONS


def _render_html_iframe(html_content: str, *, height: int) -> None:
    document = (
        "<!doctype html><html><head><meta charset=\"utf-8\"></head><body>"
        f"{html_content}"
        "</body></html>"
    )
    encoded = base64.b64encode(document.encode("utf-8")).decode("ascii")
    st.iframe(f"data:text/html;base64,{encoded}", height=height)


def _render_sidebar() -> tuple[str, bool, Optional[SMTPSettings]]:
    with st.sidebar:
        st.header("Email Settings")
        email_subject = st.text_input("Email Subject", value=DEFAULT_SUBJECT_TEMPLATE)
        dry_run = st.toggle("Dry Run", value=True, help="Only preview prepared emails without sending.")

        st.divider()
        st.caption("SMTP for Gmail is configured for SSL port 465.")

        try:
            smtp_settings = load_smtp_settings(subject_template=email_subject, require_password=not dry_run)
            st.success(f"Loaded credentials for {smtp_settings.sender_email}")
        except ValueError as exc:
            st.warning(str(exc))
            smtp_settings = None

    return email_subject, dry_run, smtp_settings


def _render_system_status() -> tuple[list[EmailAttachment], list[str]]:
    st.sidebar.subheader("System Status")

    attachments, missing_files = _load_required_cv_attachments_from_folder()
    cvs_root = os.path.join(os.getcwd(), "cvs")
    expected_paths = [os.path.join(cvs_root, file_name) for file_name in MANDATORY_CV_FILES]
    all_exist = all(os.path.exists(path) for path in expected_paths)

    if all_exist and not missing_files:
        st.sidebar.success("✅ CVs Ready")
    else:
        st.sidebar.error("❌ CVs MISSING in /cvs/ folder")

    for file_name, file_path in zip(MANDATORY_CV_FILES, expected_paths):
        if os.path.exists(file_path):
            st.sidebar.write(f"✅ {file_name}")
        else:
            st.sidebar.write(f"❌ {file_name}")

    return attachments, missing_files


def _render_file_preview(df: pd.DataFrame) -> None:
    st.subheader("Loaded Data Preview")
    st.dataframe(df, width="stretch")


def _guess_mime_type(file_path: Path) -> str:
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        return "application/pdf"
    if suffix == ".doc":
        return "application/msword"
    if suffix == ".docx":
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return "application/octet-stream"


def _load_required_cv_attachments_from_folder() -> tuple[list[EmailAttachment], list[str]]:
    attachments: list[EmailAttachment] = []
    missing_files: list[str] = []

    for file_name in MANDATORY_CV_FILES:
        file_path = Path(os.path.join(os.getcwd(), "cvs", file_name))
        if not os.path.exists(file_path):
            missing_files.append(file_name)
            continue

        if file_path.suffix.lower() not in ALLOWED_CV_EXTENSIONS:
            missing_files.append(file_name)
            continue

        attachments.append(
            EmailAttachment(
                filename=file_path.name,
                content=file_path.read_bytes(),
                mime_type=_guess_mime_type(file_path),
            )
        )

    return attachments, missing_files


def main() -> None:
    st.set_page_config(page_title="Email Automator", layout="wide")
    st.title("Email Automator")
    st.info("Upload an Excel or CSV file, review the preview, and generate personalized emails.")

    email_subject, dry_run, smtp_settings = _render_sidebar()

    uploaded_file = st.file_uploader("Upload spreadsheet (.xlsx or .csv)", type=["xlsx", "csv"])

    if uploaded_file is None:
        st.stop()

    try:
        df = load_uploaded_file(uploaded_file)
    except Exception as exc:
        st.error(f"Failed to load file: {exc}")
        st.stop()

    if df.empty:
        st.warning("The uploaded file has no rows.")
        st.stop()

    _render_file_preview(df)

    st.subheader("Message Template")
    st.write(
        "Use column names in braces, for example {target_company}, {target_city}, {custom_note}."
    )
    html_template = st.text_area("Email text (HTML - primary)", value=DEFAULT_HTML_TEMPLATE, height=420)
    with st.expander("Plain text fallback (reserve)"):
        plain_template = st.text_area(
            "Email text (plain fallback)",
            value=DEFAULT_PLAIN_TEMPLATE,
            height=240,
            help="Used only as fallback for clients that cannot render HTML.",
        )

    preview_row = df.iloc[0].to_dict()
    preview_subject = render_template(email_subject, {str(k): v for k, v in preview_row.items()})
    preview_html = render_html_template(html_template, {str(k): v for k, v in preview_row.items()})

    st.subheader("Real Preview")
    st.caption(preview_subject)
    _render_html_iframe(preview_html, height=600)

    st.subheader("Attachments")
    attachments, missing_files = _render_system_status()
    if not missing_files:
        st.success(
            "Loaded required attachments from /cvs: "
            + ", ".join(attachment.filename for attachment in attachments)
        )
    else:
        st.sidebar.warning("Error: CV files not found in /cvs folder!")
        st.warning("Error: CV files not found in /cvs folder!")

    recipient_column = st.selectbox(
        "Recipient email column",
        options=list(df.columns),
        index=list(df.columns).index("Email") if "Email" in df.columns else 0,
    )

    col_run, col_reset = st.columns(2)

    with col_run:
        run_label = "🧪 Run dry run" if dry_run else "🚀 Send emails"
        if st.button(run_label, type="primary"):
            if smtp_settings is None:
                st.error("Set EMAIL_USER and EMAIL_PASSWORD in .env first.")
                st.stop()
            if missing_files:
                st.error("Error: CV files not found in /cvs folder!")
                st.stop()

            progress_bar = st.progress(0.0)
            status_text = st.empty()

            def update_progress(value: float) -> None:
                progress_bar.progress(value)

            def update_status(message: str) -> None:
                status_text.text(message)

            try:
                result = process_email_batch(
                    df=df,
                    settings=smtp_settings,
                    plain_template=plain_template,
                    html_template=html_template,
                    recipient_column=recipient_column,
                    attachments=attachments,
                    dry_run=dry_run,
                    on_progress=update_progress,
                    on_status=update_status,
                )
            except KeyError as exc:
                st.error(f"Template/data mismatch: {exc}")
                st.stop()
            except Exception as exc:
                st.error(f"Unexpected error: {exc}")
                st.exception(exc)
                st.stop()

            st.success(f"Done. Sent: {result.sent}, failed: {result.failed}.")
            if result.errors:
                st.error("\n".join(result.errors))

    with col_reset:
        if st.button("🔄 Reload"):
            st.rerun()
