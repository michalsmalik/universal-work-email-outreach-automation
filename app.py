"""Streamlit application entrypoint for city discovery and outreach workflows.

This module orchestrates the modular app UI, discovery workflow, persistence,
and outreach sending while delegating reusable business logic to src.core_logic.
"""

from __future__ import annotations

import json
import os
import random
import re
import smtplib
import threading
import time
from types import ModuleType
from dataclasses import dataclass
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape as html_escape, unescape as html_unescape
from io import BytesIO
from pathlib import Path
from typing import Any, cast

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from src.ai_handler import generate_custom_note_with_diagnostics
from src.discovery_engine import build_discovery_dataframe
from src.core_logic import (
    append_sent_email_registry,
    is_valid_email,
    load_sent_email_registry,
)
from src import templates as templates_module

try:
    from streamlit.runtime import scriptrunner as _streamlit_scriptrunner
except Exception:
    _scriptrunner: ModuleType | None = None
else:
    _scriptrunner = _streamlit_scriptrunner


def _noop_add_script_run_context(thread: Any = None) -> None:
    return None


if _scriptrunner is None:
    add_script_run_context = _noop_add_script_run_context
else:
    add_script_run_context = getattr(
        _scriptrunner,
        "add_script_run_context",
        getattr(_scriptrunner, "add_script_run_ctx", _noop_add_script_run_context),
    )


DEFAULT_CITIES = list(getattr(templates_module, "DEFAULT_CITIES", []))
DEFAULT_EMAIL_BLACKLIST = set(getattr(templates_module, "DEFAULT_EMAIL_BLACKLIST", set()))
DEFAULT_OUTREACH_SUBJECT_TEMPLATE = str(
    getattr(templates_module, "DEFAULT_OUTREACH_SUBJECT_TEMPLATE", "")
)
DEFAULT_OUTREACH_HTML_TEMPLATE = str(
    getattr(templates_module, "DEFAULT_OUTREACH_HTML_TEMPLATE", "")
)
DEFAULT_SEARCH_QUERY_TEMPLATE = str(
    getattr(templates_module, "DEFAULT_SEARCH_QUERY_TEMPLATE", "{business_type} in {city}, {country}")
)

DEFAULT_BUSINESS_TYPE = str(getattr(templates_module, "DEFAULT_BUSINESS_TYPE", "Hotels"))
DEFAULT_COUNTRY = str(getattr(templates_module, "DEFAULT_COUNTRY", "Norway"))
TRACKER_FILE = str(getattr(templates_module, "TRACKER_FILE", "discovery_tracker.json"))
CITIES_FILE = str(getattr(templates_module, "CITIES_FILE", "cities.txt"))
SENT_EMAILS_REGISTRY_FILE = str(
    getattr(templates_module, "SENT_EMAILS_REGISTRY_FILE", "sent_emails_registry.json")
)
SEARCHING_CONTACTS_GIF = str(Path("assets") / "searching_contacts.gif")
EMAIL_SEND_GIF = str(Path("assets") / "email_send.gif")
NAV_ORDER = ["City Generator", "Discovery", "Outreach"]
NAV_QUERY_PARAM = str(getattr(templates_module, "NAV_QUERY_PARAM", "module"))


@dataclass(frozen=True)
class AppConfig:
    sender_name: str
    sender_phone: str
    linkedin_url: str
    linkedin_label: str
    sender_email: str
    sender_password: str
    maps_api_key: str
    gemini_api_key: str
    search_query_template: str
    attachments: list[str]
    business_type_default: str
    country_default: str
    email_blacklist: tuple[str, ...]


def parse_csv_env(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def load_config() -> AppConfig:
    load_dotenv()

    extra_blacklist = {item.lower() for item in parse_csv_env(os.getenv("TRASH_EMAIL_PATTERNS", ""))}
    merged_blacklist = tuple(sorted({*DEFAULT_EMAIL_BLACKLIST, *extra_blacklist}))

    return AppConfig(
        sender_name=(
            os.getenv("SENDER_NAME", str(getattr(templates_module, "DEFAULT_SENDER_NAME", "Your Full Name")))
            .strip()
            or str(getattr(templates_module, "DEFAULT_SENDER_NAME", "Your Full Name"))
        ),
        sender_phone=(
            os.getenv("SENDER_PHONE", str(getattr(templates_module, "DEFAULT_SENDER_PHONE", "+0000000000")))
            .strip()
            or str(getattr(templates_module, "DEFAULT_SENDER_PHONE", "+0000000000"))
        ),
        linkedin_url=os.getenv("LINKEDIN_URL", "").strip(),
        linkedin_label=(
            os.getenv("LINKEDIN_LABEL", str(getattr(templates_module, "DEFAULT_LINKEDIN_LABEL", "LinkedIn Profile")))
            .strip()
            or str(getattr(templates_module, "DEFAULT_LINKEDIN_LABEL", "LinkedIn Profile"))
        ),
        sender_email=os.getenv("EMAIL_USER", "").strip(),
        sender_password=os.getenv("EMAIL_PASSWORD", "").strip(),
        maps_api_key=os.getenv("MAPS_API_KEY", "").strip(),
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        search_query_template=(
            os.getenv("SEARCH_QUERY", DEFAULT_SEARCH_QUERY_TEMPLATE).strip()
            or DEFAULT_SEARCH_QUERY_TEMPLATE
        ),
        attachments=parse_csv_env(
            os.getenv("ATTACHMENTS", ",".join(getattr(templates_module, "DEFAULT_ATTACHMENTS", ())))
        ),
        business_type_default=os.getenv("BUSINESS_TYPE", DEFAULT_BUSINESS_TYPE).strip() or DEFAULT_BUSINESS_TYPE,
        country_default=os.getenv("COUNTRY", DEFAULT_COUNTRY).strip() or DEFAULT_COUNTRY,
        email_blacklist=merged_blacklist,
    )


def to_xlsx_bytes(df: pd.DataFrame, sheet_name: str = "Sheet1") -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
    return output.getvalue()


def build_html_body(plain_text: str, config: AppConfig) -> str:
    escaped = html_escape(plain_text).replace("\n", "<br>")
    lowered = plain_text.lower()
    if "best regards" in lowered and ("phone:" in lowered or "linkedin" in lowered):
        if config.linkedin_url and config.linkedin_label.lower() in lowered:
            icon_html = (
                f'<a href="{html_escape(config.linkedin_url)}" target="_blank" '
                'style="display:inline-flex;align-items:center;text-decoration:none;">'
                '<img '
                'src="https://cdn-icons-png.flaticon.com/512/174/174857.png" '
                'alt="LinkedIn Profile" '
                'width="24" height="24" '
                'style="display:inline-block;border:0;vertical-align:middle;"/>'
                '</a>'
            )
            escaped = escaped.replace(config.linkedin_label, icon_html)
        return f"<p>{escaped}</p>"

    signature_lines = [
        config.sender_name,
        f"Phone: {config.sender_phone}" if config.sender_phone else "",
    ]
    signature_html = "<br>".join(line for line in signature_lines if line)
    linkedin_html = (
        f'<br><a href="{html_escape(config.linkedin_url)}" target="_blank" '
        'style="display:inline-flex;align-items:center;text-decoration:none;">'
        '<img '
        'src="https://cdn-icons-png.flaticon.com/512/174/174857.png" '
        'alt="LinkedIn Profile" '
        'width="24" height="24" '
        'style="display:inline-block;border:0;vertical-align:middle;"/>'
        '</a>'
        if config.linkedin_url
        else ""
    )
    return f"<p>{escaped}</p><p>{signature_html}{linkedin_html}</p>"


def html_to_plain_text(html_body: str) -> str:
    text = re.sub(r"(?i)<br\\s*/?>", "\n", html_body)
    text = re.sub(r"(?i)</p>", "\n\n", text)
    text = re.sub(r"(?i)<li>\\s*", "- ", text)
    text = re.sub(r"(?i)</li>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def render_html_iframe(html_content: str, *, height: int) -> None:
    # st.html is the modern Streamlit way for safe HTML rendering in previews.
    st.html(html_content)


def send_email(
    server: smtplib.SMTP_SSL,
    sender_email: str,
    recipient_email: str,
    subject: str,
    plain_body: str,
    html_body: str,
    attachment_parts: list[MIMEApplication],
) -> None:
    msg = MIMEMultipart("mixed")
    msg["From"] = sender_email
    msg["To"] = recipient_email
    msg["Subject"] = subject

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(plain_body, "plain", "utf-8"))
    alt.attach(MIMEText(html_body, "html", "utf-8"))
    msg.attach(alt)

    for part in attachment_parts:
        msg.attach(part)

    server.send_message(msg)


def load_cities() -> list[str]:
    path = Path(CITIES_FILE)
    if path.exists():
        lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
        cities = [line for line in lines if line and not line.startswith("#")]
        if cities:
            return cities
    return list(DEFAULT_CITIES)


def load_tracker(cities: list[str]) -> dict[str, bool]:
    default = {city: False for city in cities}
    path = Path(TRACKER_FILE)

    if not path.exists():
        path.write_text(json.dumps(default, indent=2), encoding="utf-8")
        return default

    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        path.write_text(json.dumps(default, indent=2), encoding="utf-8")
        return default

    if not isinstance(loaded, dict):
        path.write_text(json.dumps(default, indent=2), encoding="utf-8")
        return default

    normalized = {city: bool(loaded.get(city, False)) for city in cities}
    if normalized != loaded:
        path.write_text(json.dumps(normalized, indent=2), encoding="utf-8")

    return normalized


def save_tracker(tracker: dict[str, bool]) -> None:
    Path(TRACKER_FILE).write_text(json.dumps(tracker, indent=2), encoding="utf-8")


def check_attachments(config: AppConfig) -> tuple[list[Path], list[str]]:
    cvs_dir = Path.cwd() / "cvs"
    existing: list[Path] = []
    missing: list[str] = []

    for file_name in config.attachments:
        target = cvs_dir / file_name
        if target.exists():
            existing.append(target)
        else:
            missing.append(file_name)

    return existing, missing


def get_active_module() -> str:
    query_value = st.query_params.get(NAV_QUERY_PARAM, NAV_ORDER[0])
    if isinstance(query_value, list):
        query_value = query_value[0] if query_value else NAV_ORDER[0]
    active = str(query_value).strip()
    return active if active in NAV_ORDER else NAV_ORDER[0]


def set_active_module(target: str) -> None:
    if target not in NAV_ORDER:
        return
    st.query_params[NAV_QUERY_PARAM] = target
    st.rerun()


def render_sidebar(config: AppConfig, active_module: str) -> tuple[AppConfig, list[Path], list[str]]:
    st.sidebar.header("Configuration Status")

    attachment_paths, missing_files = check_attachments(config)
    registry_size = len(load_sent_email_registry(SENT_EMAILS_REGISTRY_FILE))
    gemini_ok = bool(config.gemini_api_key)
    smtp_ok = bool(config.sender_email and config.sender_password)
    cvs_ok = bool(config.attachments) and not missing_files

    st.sidebar.write("✅ Gemini API Key" if gemini_ok else "❌ Gemini API Key")
    st.sidebar.write("✅ SMTP Config" if smtp_ok else "❌ SMTP Config")
    st.sidebar.write(f"✅ {registry_size} Emails in Registry")
    st.sidebar.write("✅ CVs found in /cvs/" if cvs_ok else "❌ CVs missing")

    if not config.attachments:
        st.sidebar.warning("No ATTACHMENTS configured in .env")
    for file_name in config.attachments:
        if (Path.cwd() / "cvs" / file_name).exists():
            st.sidebar.write(f"✅ {file_name}")
        else:
            st.sidebar.write(f"❌ {file_name}")

    st.sidebar.divider()
    st.sidebar.header("Navigation")
    selected_module = st.sidebar.radio(
        "Navigation",
        options=NAV_ORDER,
        index=NAV_ORDER.index(active_module),
        label_visibility="collapsed",
    )
    if selected_module != active_module:
        st.query_params[NAV_QUERY_PARAM] = selected_module
        st.rerun()

    st.sidebar.divider()
    st.sidebar.header("API Settings")
    maps_api_key = st.sidebar.text_input("MAPS_API_KEY", value=config.maps_api_key, type="password")
    gemini_api_key = st.sidebar.text_input("GEMINI_API_KEY", value=config.gemini_api_key, type="password")

    updated_config = AppConfig(
        sender_name=config.sender_name,
        sender_phone=config.sender_phone,
        linkedin_url=config.linkedin_url,
        linkedin_label=config.linkedin_label,
        sender_email=config.sender_email,
        sender_password=config.sender_password,
        maps_api_key=maps_api_key.strip(),
        gemini_api_key=gemini_api_key.strip(),
        search_query_template=config.search_query_template,
        attachments=config.attachments,
        business_type_default=config.business_type_default,
        country_default=config.country_default,
        email_blacklist=config.email_blacklist,
    )

    return updated_config, attachment_paths, missing_files


@st.fragment(run_every=1)
def render_discovery_runtime_panel(city: str, tracker: dict[str, bool]) -> None:
    runtime = st.session_state.get("discovery_runtime")
    if not isinstance(runtime, dict):
        return

    runtime_state = cast(dict[str, Any], runtime)
    runtime_thread = runtime_state.get("thread")
    is_running = isinstance(runtime_thread, threading.Thread) and runtime_thread.is_alive()

    if is_running:
        st.progress(float(runtime_state.get("progress", 0.0)))
        st.info(str(runtime_state.get("status", "Discovery is running...")))
        if st.button("Stop", type="primary", key="discovery_stop_button"):
            stop_event = runtime_state.get("stop_event")
            if isinstance(stop_event, threading.Event):
                stop_event.set()
            st.rerun()
        return

    if bool(runtime_state.get("done")) and not bool(runtime_state.get("finalized")):
        runtime_state["finalized"] = True
        if runtime_state.get("error"):
            st.error(f"Discovery failed: {runtime_state['error']}")
        else:
            result_df = runtime_state.get("result_df")
            if isinstance(result_df, pd.DataFrame):
                st.session_state["ready_to_send_df"] = result_df
                diagnostics = runtime_state.get("diagnostics")
                if isinstance(diagnostics, dict):
                    st.session_state["gemini_note_diagnostics"] = diagnostics
                if bool(runtime_state.get("stopped")):
                    st.warning(f"Discovery stopped. Collected {len(result_df)} valid rows.")
                else:
                    tracker[str(runtime_state.get("city", city.strip()))] = True
                    save_tracker(tracker)
                    st.success(f"Discovery complete: {len(result_df)} valid rows.")
        st.rerun()
def render_discovery(config: AppConfig, tracker: dict[str, bool]) -> None:
    st.subheader("Discovery")
    st.caption("Discover potential targets with Google Places and website scraping.")
    search_animation = st.sidebar.empty()

    with st.expander("Gemini Note Diagnostics", expanded=False):
        diag = st.session_state.get("gemini_note_diagnostics", {})
        model_diag = st.session_state.get("gemini_last_model_diagnostics", {})
        if isinstance(diag, dict) and diag:
            st.write(f"AI enabled in last run: {'yes' if diag.get('ai_enabled') else 'no'}")
            st.write(f"GEMINI_API_KEY present: {'yes' if diag.get('api_key_present') else 'no'}")
            st.write(f"Generation config: {diag.get('generation_config')}")
            st.write(f"AI notes: {diag.get('ai_notes', 0)}")
            st.write(f"Fallback notes: {diag.get('fallback_notes', 0)}")
            reasons = diag.get("fallback_reasons", {})
            if isinstance(reasons, dict) and reasons:
                st.caption("Fallback reasons (last run):")
                st.json(reasons)
            if isinstance(model_diag, dict) and model_diag:
                discovered = model_diag.get("discovered_models", [])
                attempted = model_diag.get("candidate_models", [])
                if isinstance(discovered, list):
                    st.caption("Discovered models for this key:")
                    st.write(", ".join(str(item) for item in discovered[:15]) or "none")
                if isinstance(attempted, list):
                    st.caption("Attempted models (in order):")
                    st.write(", ".join(str(item) for item in attempted[:15]) or "none")
                list_error = model_diag.get("list_models_error", "")
                if list_error:
                    st.caption(f"Model listing status: {list_error}")
                err_items = model_diag.get("errors", [])
                if isinstance(err_items, list) and err_items:
                    st.caption("Model call errors:")
                    st.write("; ".join(str(item) for item in err_items))
        else:
            st.caption("No diagnostics yet. Run Discovery once to collect Gemini stats.")

    pending_city = str(st.session_state.pop("pending_discovery_city", "")).strip()
    if pending_city:
        st.session_state["discovery_city"] = pending_city

    city = st.text_input("City", key="discovery_city")
    business_type = st.text_input("Business Type", value=config.business_type_default)
    country = st.text_input("Country", value=config.country_default)
    query_template = st.text_input("Search Query Template", value=config.search_query_template)
    selected_accommodation_types = st.multiselect(
        "Select Accommodation Types",
        options=["Hotels", "Camping", "Fjellstue", "Gjestegård", "Rorbu", "Hostels", "Resorts"],
        default=["Hotels", "Camping", "Fjellstue", "Gjestegård", "Rorbu", "Hostels", "Resorts"],
    )
    max_results = st.number_input("Max Discovery Rows", min_value=1, max_value=200, value=20, step=1)
    use_ai_note = st.checkbox("Enable Gemini custom notes", value=True)

    if st.button("Test Gemini note generation"):
        test_note, source, reason = generate_custom_note_with_diagnostics(
            company="Demo Hotel",
            city=city.strip() or "Oslo",
            gemini_api_key=config.gemini_api_key,
            use_ai_personalization=use_ai_note,
        )
        if source == "ai":
            model_info = reason.replace("model:", "") if reason.startswith("model:") else reason
            st.success(f"Gemini is working. AI note generated successfully with {model_info}.")
        else:
            st.warning(f"Gemini fallback was used: {reason}")
        st.write(f"Sample note: {test_note}")

    runtime = st.session_state.get("discovery_runtime")
    runtime_state = runtime if isinstance(runtime, dict) else None
    runtime_thread = runtime_state.get("thread") if runtime_state is not None else None
    is_running = isinstance(runtime_thread, threading.Thread) and runtime_thread.is_alive()

    if is_running:
        if Path(SEARCHING_CONTACTS_GIF).exists():
            with search_animation.container():
                st.image(
                    SEARCHING_CONTACTS_GIF,
                    caption="Searching and verifying contact emails...",
                    width=220,
                )
        else:
            search_animation.info("Searching and verifying contact emails...")
    else:
        search_animation.empty()

    render_discovery_runtime_panel(city=city, tracker=tracker)

    if not is_running and st.button("Run Discovery", type="primary"):
        if not city.strip():
            st.error("City is required.")
            return
        if not config.maps_api_key:
            st.error("MAPS_API_KEY is required.")
            return

        stop_event = threading.Event()
        discovery_runtime = {
            "progress": 0.0,
            "status": "Starting discovery...",
            "stop_event": stop_event,
            "result_df": None,
            "error": "",
            "done": False,
            "stopped": False,
            "city": city.strip(),
            "diagnostics": {},
        }

        def _worker() -> None:
            runtime_state = cast(dict[str, Any], discovery_runtime)
            try:
                blocked_emails = load_sent_email_registry(SENT_EMAILS_REGISTRY_FILE)
                diagnostics: dict[str, Any] = {}
                discovered = build_discovery_dataframe(
                    city=city.strip(),
                    business_type=business_type.strip() or "Business",
                    country=country.strip() or "Country",
                    query_template=query_template.strip() or DEFAULT_SEARCH_QUERY_TEMPLATE,
                    maps_api_key=config.maps_api_key,
                    gemini_api_key=config.gemini_api_key,
                    blacklist=config.email_blacklist,
                    blocked_emails=blocked_emails,
                    max_results=int(max_results),
                    use_ai_personalization=use_ai_note,
                    accommodation_types=selected_accommodation_types,
                    on_progress=lambda value: runtime_state.__setitem__("progress", value),
                    on_status=lambda message: runtime_state.__setitem__("status", message),
                    should_stop=stop_event.is_set,
                    diagnostics_out=diagnostics,
                )
                runtime_state["result_df"] = discovered
                runtime_state["diagnostics"] = diagnostics
                runtime_state["stopped"] = stop_event.is_set()
            except Exception as exc:
                runtime_state["error"] = str(exc)
            finally:
                runtime_state["done"] = True

        runtime_state = cast(dict[str, Any], discovery_runtime)
        runtime_state["thread"] = threading.Thread(target=_worker, name="discovery-worker", daemon=True)
        st.session_state["discovery_runtime"] = runtime_state
        add_script_run_context(runtime_state["thread"])
        runtime_state["thread"].start()
        st.rerun()

    ready_df: Any = st.session_state.get("ready_to_send_df")
    if isinstance(ready_df, pd.DataFrame) and not ready_df.empty:
        st.dataframe(ready_df, width="stretch")
        xlsx_bytes = to_xlsx_bytes(ready_df, sheet_name="ReadyToSend")
        if st.download_button(
            "Download Ready-to-Send XLSX",
            data=xlsx_bytes,
            file_name="ready_to_send.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ):
            new_entries = append_sent_email_registry(
                [
                    str(email).strip().lower()
                    for email in ready_df.get("email", pd.Series(dtype=str)).tolist()
                ],
                SENT_EMAILS_REGISTRY_FILE,
            )
            st.success(f"Global registry updated with {new_entries} new email(s).")
            set_active_module("Outreach")


def render_outreach(config: AppConfig, attachment_paths: list[Path], missing_files: list[str]) -> None:
    st.subheader("Outreach")
    st.caption("Send personalized outreach emails from uploaded or discovered data.")

    preloaded_df = st.session_state.get("ready_to_send_df")
    uploaded = st.file_uploader("Upload Ready-to-Send file (.xlsx/.csv)", type=["xlsx", "csv"])

    df: pd.DataFrame | None = None
    if uploaded is not None:
        name = uploaded.name.lower()
        if name.endswith(".xlsx"):
            df = pd.read_excel(uploaded, engine="openpyxl")
        elif name.endswith(".csv"):
            df = pd.read_csv(uploaded)
    elif isinstance(preloaded_df, pd.DataFrame):
        df = preloaded_df.copy()
        st.info("Using in-memory results from Discovery module.")

    subject_template = st.text_input("Subject Template", value=DEFAULT_OUTREACH_SUBJECT_TEMPLATE)
    identity = templates_module.OutreachIdentity(
        sender_name=config.sender_name,
        sender_phone=config.sender_phone,
        linkedin_label=config.linkedin_label,
    )
    default_html_body = templates_module.with_identity(DEFAULT_OUTREACH_HTML_TEMPLATE, identity).replace(config.linkedin_label, (f'<a href="{html_escape(config.linkedin_url)}" target="_blank" style="text-decoration:none;display:inline-flex;align-items:center;gap:6px;"><img src="https://cdn-icons-png.flaticon.com/512/174/174857.png" alt="LinkedIn" width="18" height="18" style="display:inline-block;border:0;vertical-align:middle;" />{html_escape(config.linkedin_label)}</a>' if config.linkedin_url else html_escape(config.linkedin_label)))
    html_template = st.text_area("Body Template (HTML - primary)", value=default_html_body, height=380)
    with st.expander("Plain text fallback (reserve)"):
        default_plain_fallback = templates_module.default_outreach_plain_template(identity)
        plain_fallback_template = st.text_area(
            "Plain text fallback",
            value=default_plain_fallback,
            height=220,
            help="Used as a fallback for email clients that cannot render HTML.",
        )
    dry_run = st.checkbox("Dry Run (preview only)", value=False)

    if df is None:
        st.info("Upload a file or run Discovery first.")
        return
    if df.empty:
        st.warning("No rows to send.")
        return

    st.dataframe(df, width="stretch")

    recipient_column = st.selectbox(
        "Recipient Column",
        options=list(df.columns),
        index=list(df.columns).index("email") if "email" in df.columns else 0,
    )

    can_send = True
    if not config.sender_email:
        st.warning("EMAIL_USER is missing.")
        can_send = False
    if not dry_run and not config.sender_password:
        st.warning("EMAIL_PASSWORD is required for sending.")
        can_send = False
    if missing_files:
        st.warning("Missing attachments: " + ", ".join(missing_files))
        can_send = False

    if st.button("Send Outreach", type="primary", disabled=not can_send):
        sent = 0
        failed = 0
        errors: list[str] = []
        progress = st.progress(0.0)
        send_animation = st.sidebar.empty()
        if Path(EMAIL_SEND_GIF).exists():
            with send_animation.container():
                st.image(
                    EMAIL_SEND_GIF,
                    caption="Sending emails in progress...",
                    width=220,
                )
        else:
            send_animation.info("Sending emails in progress...")

        attachment_parts: list[MIMEApplication] = []
        if not dry_run:
            for file_path in attachment_paths:
                with open(file_path, "rb") as file_obj:
                    part = MIMEApplication(file_obj.read())
                part.add_header("Content-Disposition", "attachment", filename=file_path.name)
                attachment_parts.append(part)

        if dry_run:
            for index, (_, row) in enumerate(df.iterrows(), start=1):
                row_data = {str(key): value for key, value in row.to_dict().items()}
                recipient_email = str(row_data.get(recipient_column, "")).strip()
                if not is_valid_email(recipient_email, config.email_blacklist):
                    failed += 1
                    errors.append(f"Row {index}: invalid recipient email '{recipient_email}'")
                    progress.progress(index / max(len(df), 1))
                    continue

                subject = subject_template.format(**row_data)
                safe_row_data = {key: html_escape(str(value)) for key, value in row_data.items()}
                html_body = html_template.format(**safe_row_data)
                plain_body = plain_fallback_template.format(**row_data).strip()
                if not plain_body:
                    plain_body = html_to_plain_text(html_body)

                with st.expander(f"Preview: {recipient_email}"):
                    st.caption(subject)
                    render_html_iframe(html_body, height=350)
                sent += 1
                progress.progress(index / max(len(df), 1))
        else:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
                server.login(config.sender_email, config.sender_password)

                for index, (_, row) in enumerate(df.iterrows(), start=1):
                    row_data = {str(key): value for key, value in row.to_dict().items()}
                    recipient_email = str(row_data.get(recipient_column, "")).strip()
                    if not is_valid_email(recipient_email, config.email_blacklist):
                        failed += 1
                        errors.append(f"Row {index}: invalid recipient email '{recipient_email}'")
                        progress.progress(index / max(len(df), 1))
                        continue

                    subject = subject_template.format(**row_data)
                    safe_row_data = {key: html_escape(str(value)) for key, value in row_data.items()}
                    html_body = html_template.format(**safe_row_data)
                    plain_body = plain_fallback_template.format(**row_data).strip()
                    if not plain_body:
                        plain_body = html_to_plain_text(html_body)

                    try:
                        send_email(
                            server=server,
                            sender_email=config.sender_email,
                            recipient_email=recipient_email,
                            subject=subject,
                            plain_body=plain_body,
                            html_body=html_body,
                            attachment_parts=attachment_parts,
                        )
                        sent += 1
                        time.sleep(1)
                    except Exception as exc:
                        failed += 1
                        errors.append(f"Row {index}: {exc}")

                    progress.progress(index / max(len(df), 1))

        send_animation.empty()

        st.success(f"Completed. Success: {sent}, Failed: {failed}")
        if errors:
            st.error("\n".join(errors))


def render_city_generator() -> None:
    st.subheader("City Generator")
    st.caption("Track city usage and push generated city to Discovery.")

    cities = load_cities()
    tracker = load_tracker(cities)

    available = sorted([city for city, used in tracker.items() if not used])
    contacted = sorted([city for city, used in tracker.items() if used])

    st.metric("Progress", f"{len(contacted)} / {len(cities)}")
    st.progress((len(contacted) / len(cities)) if cities else 0.0)

    with st.expander(f"Available Cities ({len(available)})"):
        if available:
            st.dataframe(pd.DataFrame({"city": available}), width="stretch")
        else:
            st.info("No available cities.")

    with st.expander(f"Completed Cities ({len(contacted)})"):
        if contacted:
            st.dataframe(pd.DataFrame({"city": contacted}), width="stretch")
        else:
            st.info("No completed cities yet.")

    if st.button("Generate City", type="secondary", disabled=not bool(available)):
        generated = random.choice(available)
        tracker[generated] = True
        save_tracker(tracker)
        st.session_state["generated_city"] = generated
        st.rerun()

    generated_city = str(st.session_state.get("generated_city", "")).strip()
    if generated_city:
        st.success(f"Generated city: {generated_city}")
        st.markdown(
            """
            <style>
            @keyframes discoveryPulse {
                0% { box-shadow: 0 0 0 0 rgba(30, 136, 229, 0.50); }
                70% { box-shadow: 0 0 0 12px rgba(30, 136, 229, 0); }
                100% { box-shadow: 0 0 0 0 rgba(30, 136, 229, 0); }
            }
            div[data-testid="stButton"] button[kind="primary"] {
                background: linear-gradient(180deg, #1e88e5 0%, #1565c0 100%) !important;
                border: 1px solid #0d47a1 !important;
                color: #ffffff !important;
                animation: discoveryPulse 1.7s infinite;
            }
            div[data-testid="stButton"] button[kind="primary"]:hover {
                filter: brightness(1.05);
            }
            </style>
            """,
            unsafe_allow_html=True,
        )
        if st.button("Use in Discovery", type="primary", key="use_in_discovery_btn"):
            st.session_state["pending_copy_text"] = generated_city
            st.session_state["pending_discovery_city"] = generated_city
            set_active_module("Discovery")

    if st.button("Reset Discovery Tracker"):
        save_tracker({city: False for city in cities})
        st.session_state.pop("generated_city", None)
        st.success("Tracker reset.")
        st.rerun()


def render_clipboard_copy_if_pending() -> None:
    text = str(st.session_state.pop("pending_copy_text", "")).strip()
    if not text:
        return

    payload = json.dumps(text)
    render_html_iframe(
        f"<script>navigator.clipboard.writeText({payload}).catch(() => {{}});</script>",
        height=1,
    )


def main() -> None:
    config = load_config()

    st.set_page_config(page_title="Universal Outreach Automator", layout="wide")
    st.title("Universal Outreach Automator")
    st.caption("Simple and stable workflow for city generation, discovery, and outreach.")

    render_clipboard_copy_if_pending()

    active = get_active_module()
    config, attachment_paths, missing_files = render_sidebar(config, active)

    if active == "City Generator":
        render_city_generator()
    elif active == "Discovery":
        tracker = load_tracker(load_cities())
        render_discovery(config, tracker)
    elif active == "Outreach":
        render_outreach(config, attachment_paths, missing_files)


if __name__ == "__main__":
    main()
